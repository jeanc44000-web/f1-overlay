
import logging
import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


logger = logging.getLogger(__name__)

series_bp = Blueprint("series", __name__)

SERIES = {
    "f2": {
        "name": "FIA Formula 2",
        "url": "https://www.fiaformula2.com/livetiming/index.html",
        "template": "f2.html",
    },
    "academy": {
        "name": "F1 Academy",
        "url": "https://www.f1academy.com/livetiming/index.html",
        "template": "academy.html",
    },
}

CACHE = {
    key: {
        "name": config["name"],
        "connected": False,
        "status": "WAITING",
        "updated_at": None,
        "columns": [],
        "rows": [],
        "error": None,
        "page_text": "",
    }
    for key, config in SERIES.items()
}

CACHE_LOCK = threading.Lock()
WORKER_STARTED = False


def get_page_data(page):
    """
    Lit les tableaux HTML classiques et les lignes ARIA
    éventuellement utilisées par les pages de chronométrage.
    """
    return page.evaluate("""
        () => {
            const output = [];

            // Tableaux HTML classiques
            document.querySelectorAll("table").forEach((table) => {
                const rows = Array.from(table.querySelectorAll("tr"))
                    .map((tr) =>
                        Array.from(tr.querySelectorAll("th, td"))
                            .map((cell) => cell.innerText.trim())
                    )
                    .filter((row) => row.some((cell) => cell));

                if (rows.length) {
                    output.push({
                        type: "table",
                        rows: rows
                    });
                }
            });

            // Tableaux construits avec des éléments ARIA
            document.querySelectorAll(
                '[role="grid"], [role="table"]'
            ).forEach((table) => {
                const rows = Array.from(
                    table.querySelectorAll('[role="row"]')
                )
                    .map((row) =>
                        Array.from(
                            row.querySelectorAll(
                                '[role="gridcell"], [role="cell"], [role="columnheader"]'
                            )
                        ).map((cell) => cell.innerText.trim())
                    )
                    .filter((row) => row.some((cell) => cell));

                if (rows.length) {
                    output.push({
                        type: "aria",
                        rows: rows
                    });
                }
            });

            // Certaines pages affichent les lignes sans tableau HTML.
            if (!output.length) {
                const rows = Array.from(
                    document.querySelectorAll(
                        '[class*="row"], [class*="Row"]'
                    )
                )
                    .map((element) => element.innerText.trim())
                    .filter((text) => text && text.length < 500);

                if (rows.length) {
                    output.push({
                        type: "text",
                        rows: rows.map((text) => [text])
                    });
                }
            }

            return {
                tables: output,
                pageTitle: document.title,
                pageText: document.body
                    ? document.body.innerText.slice(0, 3000)
                    : ""
            };
        }
    """)


def normalize_table(table):
    raw_rows = table.get("rows", [])

    if not raw_rows:
        return [], []

    # La première ligne sert d'en-tête si elle ressemble à un en-tête.
    first = raw_rows[0]
    header_words = (
        "pos", "position", "driver", "pilote", "gap",
        "interval", "time", "lap", "team", "name"
    )

    first_text = " ".join(str(cell).lower() for cell in first)

    if any(word in first_text for word in header_words):
        columns = [str(cell).strip() for cell in first]
        data_rows = raw_rows[1:]
    else:
        columns = []
        data_rows = raw_rows

    rows = []

    for raw_row in data_rows:
        cells = [str(cell).strip() for cell in raw_row]

        if not any(cells):
            continue

        rows.append({
            "cells": cells
        })

    return columns, rows


def extract_timing_data(page):
    result = get_page_data(page)

    all_rows = []
    columns = []

    for table in result.get("tables", []):
        table_columns, table_rows = normalize_table(table)

        if len(table_rows) > len(all_rows):
            all_rows = table_rows
            columns = table_columns

    return {
        "columns": columns,
        "rows": all_rows,
        "page_title": result.get("pageTitle", ""),
        "page_text": result.get("pageText", ""),
    }


def update_cache(series_key, data, connected=True, error=None):
    with CACHE_LOCK:
        CACHE[series_key].update({
            "connected": connected,
            "status": "LIVE" if connected and data.get("rows") else (
                "CONNECTED - NO TABLE" if connected else "DISCONNECTED"
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "columns": data.get("columns", []),
            "rows": data.get("rows", []),
            "page_text": data.get("page_text", ""),
            "error": error,
        })


def browser_worker():
    if sync_playwright is None:
        for key in SERIES:
            update_cache(
                key,
                {},
                connected=False,
                error="Playwright n'est pas installé."
            )
        return

    while True:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )

                pages = {}

                for key, config in SERIES.items():
                    try:
                        page = browser.new_page()
                        page.set_default_timeout(20000)

                        page.goto(
                            config["url"],
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )

                        page.wait_for_timeout(12000)
                        pages[key] = page

                        data = extract_timing_data(page)
                        update_cache(key, data, connected=True)

                    except Exception as error:
                        logger.exception(
                            "Erreur au chargement de %s", key
                        )
                        update_cache(
                            key,
                            {},
                            connected=False,
                            error=str(error),
                        )

                last_reload = time.time()

                while True:
                    for key, page in list(pages.items()):
                        try:
                            data = extract_timing_data(page)
                            update_cache(key, data, connected=True)

                        except Exception as error:
                            logger.exception(
                                "Erreur de lecture de %s", key
                            )
                            update_cache(
                                key,
                                {},
                                connected=False,
                                error=str(error),
                            )

                    # Recharge les pages périodiquement pour éviter
                    # de rester bloqué sur une page déconnectée.
                    if time.time() - last_reload > 300:
                        for key, page in list(pages.items()):
                            try:
                                page.reload(
                                    wait_until="domcontentloaded",
                                    timeout=60000,
                                )
                                page.wait_for_timeout(8000)
                            except Exception as error:
                                logger.warning(
                                    "Rechargement impossible pour %s: %s",
                                    key,
                                    error,
                                )

                        last_reload = time.time()

                    time.sleep(3)

        except Exception as error:
            logger.exception("Le navigateur de chronométrage a planté.")

            for key in SERIES:
                update_cache(
                    key,
                    {},
                    connected=False,
                    error=str(error),
                )

            time.sleep(10)


@series_bp.route("/f2")
def f2_page():
    return render_template("f2.html")


@series_bp.route("/f1academy")
def academy_page():
    return render_template("academy.html")


@series_bp.route("/api/f2")
def f2_api():
    with CACHE_LOCK:
        return jsonify(CACHE["f2"].copy())


@series_bp.route("/api/f1academy")
def academy_api():
    with CACHE_LOCK:
        return jsonify(CACHE["academy"].copy())


@series_bp.route("/api/series-debug")
def series_debug():
    with CACHE_LOCK:
        return jsonify({
            key: {
                "name": value["name"],
                "connected": value["connected"],
                "status": value["status"],
                "updated_at": value["updated_at"],
                "columns": value["columns"],
                "row_count": len(value["rows"]),
                "first_rows": value["rows"][:5],
                "page_text": value["page_text"],
                "error": value["error"],
            }
            for key, value in CACHE.items()
        })


def register_f2academy(app):
    global WORKER_STARTED

    app.register_blueprint(series_bp)

    if not WORKER_STARTED:
        worker = threading.Thread(
            target=browser_worker,
            daemon=True,
            name="f2-academy-timing",
        )
        worker.start()
        WORKER_STARTED = True
