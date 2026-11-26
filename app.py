"""
F1 live overlay — backend.

Se connecte au flux officiel F1 Live Timing (SignalR), garde l'état en mémoire
et le sert au front :
  - /api/stream : Server-Sent Events (push instantané, ~4 mises à jour/s max)
  - /api/live   : snapshot JSON (fallback en polling)
  - /api/debug  : état de la connexion (à regarder en cas de souci)
Ajouter ?demo=1 pour avoir de fausses données animées (test sans session live).
"""
import asyncio
import base64
import copy
import json
import math
import os
import threading
import time
import zlib
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
import websockets
from flask import Flask, Response, render_template, request, stream_with_context

app = Flask(__name__)

# --------------------------------------------------------------------------- #
# Config F1 Live Timing
# --------------------------------------------------------------------------- #
F1_HOST = "livetiming.formula1.com"
NEGOTIATE_URL = f"https://{F1_HOST}/signalr/negotiate"
CONNECT_URL = f"wss://{F1_HOST}/signalr/connect"
CONNECTION_DATA = '[{"name":"Streaming"}]'
HTTP_HEADERS = {"User-Agent": "BestHTTP", "Accept-Encoding": "gzip,identity"}

TOPICS = [
    "Heartbeat",
    "SessionInfo",
    "SessionStatus",
    "DriverList",
    "TimingData",
    "TimingAppData",
    "TrackStatus",
    "LapCount",
    "ExtrapolatedClock",
]

# L'ancien protocole SignalR est coupé côté F1 au bout de ~5 min : on
# reconnecte proactivement avant, sans perdre l'état.
ROTATE_AFTER_SECONDS = 270
NO_MESSAGE_TIMEOUT = 60

# --------------------------------------------------------------------------- #
# Données statiques (fallback / démo)
# --------------------------------------------------------------------------- #
TEAM_COLORS = {
    "McLaren": "#ff8000",
    "Ferrari": "#e8002d",
    "Red Bull": "#3671c6",
    "Mercedes": "#00d2be",
    "Aston Martin": "#229971",
    "Alpine": "#0093cc",
    "Williams": "#1868db",
    "Racing Bulls": "#6692ff",
    "Haas": "#b6babd",
    "Audi": "#f50537",
    "Cadillac": "#e6e6e6",
}

# Grille indicative (affichée tant qu'aucune session n'envoie de données).
DEFAULT_DRIVERS = [
    ("NOR", "McLaren"), ("PIA", "McLaren"),
    ("LEC", "Ferrari"), ("HAM", "Ferrari"),
    ("VER", "Red Bull"), ("HAD", "Red Bull"),
    ("RUS", "Mercedes"), ("ANT", "Mercedes"),
    ("ALO", "Aston Martin"), ("STR", "Aston Martin"),
    ("GAS", "Alpine"), ("COL", "Alpine"),
    ("ALB", "Williams"), ("SAI", "Williams"),
    ("LAW", "Racing Bulls"), ("LIN", "Racing Bulls"),
    ("OCO", "Haas"), ("BEA", "Haas"),
    ("HUL", "Audi"), ("BOR", "Audi"),
    ("PER", "Cadillac"), ("BOT", "Cadillac"),
]

SESSION_NAMES = {
    "Practice 1": "FP1",
    "Practice 2": "FP2",
    "Practice 3": "FP3",
    "Qualifying": "QUALI",
    "Sprint Qualifying": "SQ",
    "Sprint Shootout": "SQ",
    "Sprint": "SPRINT",
    "Race": "RACE",
}

TYRE_LETTERS = {
    "SOFT": "S", "MEDIUM": "M", "HARD": "H",
    "INTERMEDIATE": "I", "WET": "W",
}

# TrackStatus : 1 AllClear, 2 Yellow, 4 SC, 5 Red, 6 VSC, 7 VSC ending
TRACK_STATUS = {
    "1": ("GREEN", ""),
    "2": ("YELLOW", "YELLOW FLAG"),
    "3": ("YELLOW", "YELLOW FLAG"),
    "4": ("YELLOW", "SAFETY CAR"),
    "5": ("RED", "RED FLAG"),
    "6": ("YELLOW", "VSC"),
    "7": ("YELLOW", "VSC ENDING"),
}

# --------------------------------------------------------------------------- #
# État partagé
# --------------------------------------------------------------------------- #
lock = threading.RLock()
cond = threading.Condition(lock)

raw = {}  # topic -> état fusionné (tel que l'envoie F1)
status = {
    "connected": False,
    "last_error": "",
    "last_message": 0.0,
    "last_connect": 0.0,
    "reconnects": 0,
    "version": 0,
}


def deep_merge(target, patch):
    """Fusionne un patch F1 dans l'état. Les listes sont patchées via des
    dicts {"0": {...}, "1": {...}} (index en chaîne)."""
    for key, value in patch.items():
        if key == "_kf":
            continue
        current = target.get(key)
        if isinstance(value, dict):
            if isinstance(current, dict):
                deep_merge(current, value)
            elif isinstance(current, list):
                merge_into_list(current, value)
            else:
                target[key] = copy.deepcopy(value)
        else:
            target[key] = value


def merge_into_list(items, patch):
    for index, value in patch.items():
        if not str(index).isdigit():
            continue
        i = int(index)
        while len(items) <= i:
            items.append({})
        if isinstance(value, dict) and isinstance(items[i], dict):
            deep_merge(items[i], value)
        else:
            items[i] = value


def decode_topic(topic, payload):
    """Les topics finissant par .z sont en base64 + zlib (deflate brut)."""
    if topic.endswith(".z") and isinstance(payload, str):
        data = zlib.decompress(base64.b64decode(payload), -zlib.MAX_WBITS)
        return topic[:-2], json.loads(data.decode("utf-8-sig"))
    return topic, payload


def ingest(topic, payload, replace):
    try:
        topic, payload = decode_topic(topic, payload)
    except Exception:
        return
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            pass
    if replace or topic not in raw or not isinstance(payload, dict):
        raw[topic] = copy.deepcopy(payload)
    elif isinstance(raw[topic], dict):
        deep_merge(raw[topic], payload)
    else:
        raw[topic] = copy.deepcopy(payload)


def handle_message(message):
    """Un message SignalR peut contenir :
      - "R" : réponse au Subscribe = état complet initial {topic: données}
      - "M" : liste d'appels {"H":"Streaming","M":"feed","A":[topic, patch, ts]}
    """
    if not isinstance(message, dict):
        return
    with cond:
        initial = message.get("R")
        if isinstance(initial, dict):
            for topic, payload in initial.items():
                ingest(topic, payload, replace=True)

        for call in message.get("M") or []:
            if not isinstance(call, dict):
                continue
            if str(call.get("H", "")).lower() != "streaming":
                continue
            if call.get("M") != "feed":
                continue
            args = call.get("A") or []
            if len(args) >= 2 and isinstance(args[0], str):
                ingest(args[0], args[1], replace=False)

        status["last_message"] = time.time()
        status["version"] += 1
        cond.notify_all()


# --------------------------------------------------------------------------- #
# Helpers de formatage
# --------------------------------------------------------------------------- #
def parse_lap_time(value):
    """'1:23.456' -> 83.456  (None si invalide)."""
    if not value or not isinstance(value, str):
        return None
    try:
        if ":" in value:
            minutes, seconds = value.split(":", 1)
            return int(minutes) * 60 + float(seconds)
        return float(value)
    except ValueError:
        return None


def fmt_gap(value):
    value = str(value or "").strip()
    if not value:
        return ""
    return value if value[0] in "+-" else f"+{value}"


def fmt_seconds_gap(seconds):
    return f"+{seconds:.3f}"


def fmt_remaining(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hours, rest = divmod(total_seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_utc(value):
    try:
        value = value.replace("Z", "+00:00")
        if "." in value:
            head, tail = value.split(".", 1)
            digits = ""
            for ch in tail:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            rest = tail[len(digits):]
            value = f"{head}.{digits[:6].ljust(6, '0')}{rest}"
        return datetime.fromisoformat(value)
    except Exception:
        return None


def remaining_seconds():
    clock = raw.get("ExtrapolatedClock") or {}
    remaining = clock.get("Remaining")
    if not remaining:
        return None
    try:
        h, m, s = remaining.split(":")
        seconds = int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        return None
    if clock.get("Extrapolating"):
        started = parse_utc(clock.get("Utc", ""))
        if started:
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            seconds -= max(0, elapsed)
    return seconds


def current_tyre(stints):
    if isinstance(stints, dict):
        keys = sorted(stints, key=lambda k: int(k) if str(k).isdigit() else 0)
        items = [stints[k] for k in keys]
    elif isinstance(stints, list):
        items = stints
    else:
        return ""
    for stint in reversed(items):
        if not isinstance(stint, dict):
            continue
        compound = str(stint.get("Compound") or "").upper()
        if compound and "UNKNOWN" not in compound:
            return TYRE_LETTERS.get(compound, compound[:1])
    return ""


def team_color(info):
    colour = str(info.get("TeamColour") or "").strip().lstrip("#")
    if len(colour) == 6:
        return f"#{colour}"
    team = str(info.get("TeamName") or "")
    for known, hex_color in TEAM_COLORS.items():
        if known.lower() in team.lower():
            return hex_color
    return "#9aa0a6"


# --------------------------------------------------------------------------- #
# Construction du snapshot envoyé au front
# --------------------------------------------------------------------------- #
def placeholder_snapshot():
    drivers = []
    for index, (tla, team) in enumerate(DEFAULT_DRIVERS, start=1):
        drivers.append({
            "position": index, "tla": tla, "team": team,
            "team_color": TEAM_COLORS[team], "gap": "", "interval": "",
            "best_lap": "", "tyre": "", "pit": False, "out": False,
            "eliminated": False, "lap": "",
        })
    return drivers


def live_snapshot():
    with lock:
        driver_list = raw.get("DriverList") or {}
        timing = raw.get("TimingData") or {}
        lines = timing.get("Lines") or {}
        app_lines = (raw.get("TimingAppData") or {}).get("Lines") or {}
        session_info = raw.get("SessionInfo") or {}
        track_raw = raw.get("TrackStatus") or {}
        lap_count = raw.get("LapCount") or {}
        session_status = (raw.get("SessionStatus") or {}).get("Status", "")

        session_type = session_info.get("Type", "")
        raw_name = session_info.get("Name", "")
        is_race = session_type == "Race" or raw_name in ("Race", "Sprint")
        is_quali = session_type == "Qualifying"
        part = timing.get("SessionPart")

        entries = []
        for number, info in driver_list.items():
            if not isinstance(info, dict) or not info.get("Tla"):
                continue
            line = lines.get(str(number))
            if not isinstance(line, dict):
                continue
            try:
                position = int(line.get("Position"))
            except (TypeError, ValueError):
                continue

            interval = line.get("IntervalToPositionAhead")
            interval = interval.get("Value", "") if isinstance(interval, dict) else interval
            best = line.get("BestLapTime")
            best = best.get("Value", "") if isinstance(best, dict) else ""

            entries.append({
                "position": position,
                "tla": info["Tla"],
                "team": info.get("TeamName", ""),
                "team_color": team_color(info),
                "gap": fmt_gap(line.get("GapToLeader")) if is_race else "",
                "interval": fmt_gap(interval) if is_race else "",
                "best_lap": best,
                "tyre": current_tyre(
                    (app_lines.get(str(number)) or {}).get("Stints")
                ),
                "pit": bool(line.get("InPit")),
                "out": bool(line.get("Retired") or line.get("Stopped")) and is_race,
                "eliminated": bool(line.get("KnockedOut")),
                "lap": line.get("NumberOfLaps", ""),
            })

        entries.sort(key=lambda e: e["position"])

        # Hors course : écarts calculés à partir des meilleurs tours.
        if not is_race and entries:
            leader_best = parse_lap_time(entries[0]["best_lap"])
            previous_best = leader_best
            for entry in entries:
                own = parse_lap_time(entry["best_lap"])
                if own is not None and leader_best is not None:
                    entry["gap"] = fmt_seconds_gap(own - leader_best)
                if own is not None and previous_best is not None:
                    entry["interval"] = fmt_seconds_gap(own - previous_best)
                if own is not None:
                    previous_best = own

        # Zone d'élimination en qualifs (22 voitures : 16 puis 10 restantes).
        if is_quali and part in (1, 2):
            cutoff = {1: 16 if len(entries) >= 22 else 15, 2: 10}[part]
            for entry in entries:
                if entry["position"] > cutoff:
                    entry["eliminated"] = True

        track_code = str(track_raw.get("Status", "1"))
        track_state, track_label = TRACK_STATUS.get(track_code, ("GREEN", ""))

        seconds = remaining_seconds() if not is_race else None

        return {
            "session": {
                "name": SESSION_NAMES.get(raw_name, raw_name.upper()) or "NO SESSION",
                "status": session_status,
                "is_race": is_race,
                "part": part,
                "current_lap": lap_count.get("CurrentLap", ""),
                "total_laps": lap_count.get("TotalLaps", ""),
                "remaining": fmt_remaining(seconds) if seconds is not None else "",
            },
            "track": {"status": track_state, "label": track_label},
            "drivers": entries,
            "has_data": bool(entries),
            "connected": status["connected"],
            "last_error": status["last_error"],
        }


def demo_snapshot():
    """Fausses données animées pour tester l'overlay sans session live."""
    now = time.time()
    compounds = ["S", "M", "H", "M", "H", "I"]
    scored = []
    for index, (tla, team) in enumerate(DEFAULT_DRIVERS):
        score = index + 2.2 * math.sin(now / 9 + index * 1.7)
        scored.append((score, index, tla, team))
    scored.sort()

    drivers = []
    cumulative = 0.0
    for place, (_, index, tla, team) in enumerate(scored, start=1):
        step = 0.6 + (index * 7 % 10) / 6
        if place > 1:
            cumulative += step
        drivers.append({
            "position": place, "tla": tla, "team": team,
            "team_color": TEAM_COLORS[team],
            "gap": f"+{cumulative:.3f}" if place > 1 else "",
            "interval": f"+{step:.3f}" if place > 1 else "",
            "best_lap": "", "tyre": compounds[(index + int(now / 40)) % len(compounds)],
            "pit": int(now / 6 + index * 3) % 22 == 0,
            "out": False, "eliminated": False, "lap": 12,
        })

    return {
        "session": {
            "name": "RACE", "status": "Started", "is_race": True, "part": None,
            "current_lap": int(now / 80) % 57 + 1, "total_laps": 57, "remaining": "",
        },
        "track": {"status": "GREEN", "label": ""},
        "drivers": drivers,
        "has_data": True,
        "connected": True,
        "last_error": "",
    }


def build_snapshot(demo=False):
    if demo:
        return demo_snapshot()
    snap = live_snapshot()
    if not snap["has_data"]:
        snap["drivers"] = placeholder_snapshot()
    return snap


# --------------------------------------------------------------------------- #
# Connexion SignalR à F1
# --------------------------------------------------------------------------- #
def negotiate():
    http = requests.Session()
    response = http.get(
        NEGOTIATE_URL,
        params={"connectionData": CONNECTION_DATA, "clientProtocol": "1.5"},
        headers=HTTP_HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    token = response.json()["ConnectionToken"]

    cookie = "; ".join(f"{c.name}={c.value}" for c in http.cookies)
    if not cookie:
        set_cookie = response.headers.get("Set-Cookie", "")
        cookie = set_cookie.split(";", 1)[0] if set_cookie else ""
    return token, cookie


async def open_socket(url, headers):
    options = dict(ping_interval=None, max_size=None, open_timeout=15)
    try:
        return await websockets.connect(url, extra_headers=headers, **options)
    except TypeError:  # websockets >= 14 : paramètre renommé
        return await websockets.connect(url, additional_headers=headers, **options)


async def run_session():
    token, cookie = await asyncio.to_thread(negotiate)

    url = CONNECT_URL + "?" + urlencode({
        "clientProtocol": "1.5",
        "transport": "webSockets",
        "connectionToken": token,
        "connectionData": CONNECTION_DATA,
    })
    headers = dict(HTTP_HEADERS)
    if cookie:
        headers["Cookie"] = cookie

    websocket = await open_socket(url, headers)
    try:
        await websocket.send(json.dumps({
            "H": "Streaming", "M": "Subscribe", "A": [TOPICS], "I": 1,
        }))

        with cond:
            status["connected"] = True
            status["last_error"] = ""
            status["last_connect"] = time.time()
            status["version"] += 1
            cond.notify_all()

        started = time.time()
        while True:
            left = ROTATE_AFTER_SECONDS - (time.time() - started)
            if left <= 0:
                return  # rotation volontaire, on se reconnecte tout de suite
            try:
                frame = await asyncio.wait_for(
                    websocket.recv(), timeout=min(NO_MESSAGE_TIMEOUT, left)
                )
            except asyncio.TimeoutError:
                if time.time() - started >= ROTATE_AFTER_SECONDS:
                    return
                raise RuntimeError(
                    f"Aucun message de F1 depuis {NO_MESSAGE_TIMEOUT}s"
                )
            try:
                handle_message(json.loads(frame))
            except ValueError:
                continue
    finally:
        with cond:
            status["connected"] = False
            status["version"] += 1
            cond.notify_all()
        try:
            await websocket.close()
        except Exception:
            pass


async def f1_live_loop():
    backoff = 3
    while True:
        try:
            await run_session()
            status["reconnects"] += 1
            backoff = 3
        except Exception as error:  # noqa: BLE001
            with cond:
                status["last_error"] = f"{type(error).__name__}: {error}"
                status["version"] += 1
                cond.notify_all()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def start_f1_thread():
    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(f1_live_loop())

    threading.Thread(target=runner, daemon=True, name="f1-signalr").start()


_started = False
_start_lock = threading.Lock()


def ensure_started():
    global _started
    with _start_lock:
        if not _started:
            _started = True
            start_f1_thread()


if os.getenv("F1_DISABLE_LIVE") != "1":
    ensure_started()


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/live")
def live():
    demo = bool(request.args.get("demo"))
    response = Response(
        json.dumps(build_snapshot(demo)), mimetype="application/json"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/stream")
def stream():
    demo = bool(request.args.get("demo"))

    @stream_with_context
    def generate():
        yield "retry: 1000\n\n"
        last_payload = None
        last_sent = 0.0
        seen_version = -1
        while True:
            with cond:
                if status["version"] == seen_version:
                    cond.wait(timeout=0.5)
                seen_version = status["version"]

            wait = 0.25 - (time.time() - last_sent)  # max ~4 envois/s
            if wait > 0:
                time.sleep(wait)

            payload = json.dumps(build_snapshot(demo), separators=(",", ":"))
            if payload != last_payload or time.time() - last_sent >= 5:
                yield f"data: {payload}\n\n"
                last_payload = payload
                last_sent = time.time()

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.route("/api/debug")
def debug():
    with lock:
        now = time.time()
        timing_lines = (raw.get("TimingData") or {}).get("Lines") or {}
        last = status["last_message"]
        return {
            "connected": status["connected"],
            "last_error": status["last_error"],
            "seconds_since_last_message": round(now - last, 1) if last else None,
            "reconnects": status["reconnects"],
            "topics_received": sorted(raw.keys()),
            "drivers_in_driverlist": len(raw.get("DriverList") or {}),
            "drivers_in_timing": len(timing_lines),
            "session_info": raw.get("SessionInfo"),
            "session_status": raw.get("SessionStatus"),
            "track_status": raw.get("TrackStatus"),
        }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), threaded=True)
