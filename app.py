
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template
from signalrcore.hub_connection_builder import HubConnectionBuilder
from fastf1.internals.f1account import get_token

app = Flask(__name__)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("f1-overlay")

F1_SIGNALR_URL = "wss://livetiming.formula1.com/signalrcore"
F1_NEGOTIATE_URL = "https://livetiming.formula1.com/signalrcore/negotiate"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

TOPICS = [
    "Heartbeat",
    "AudioStreams",
    "DriverList",
    "ExtrapolatedClock",
    "RaceControlMessages",
    "SessionInfo",
    "SessionStatus",
    "TeamRadio",
    "TimingAppData",
    "TimingStats",
    "TrackStatus",
    "WeatherData",
    "Position.z",
    "CarData.z",
    "ContentStreams",
    "SessionData",
    "TimingData",
    "TopThree",
    "RcmSeries",
    "LapCount",
]

TEAM_COLORS = {
    "McLaren": "#ff8000",
    "Ferrari": "#e8002d",
    "Red Bull": "#1e2a62",
    "Mercedes": "#00d2be",
    "Aston Martin": "#00665e",
    "Alpine": "#0093cc",
    "Williams": "#1868db",
    "Racing Bulls": "#6692ff",
    "Haas": "#b6babd",
    "Audi": "#bb0a30",
    "Cadillac": "#ececec",
}

DEFAULT_DRIVERS = [
    {"position": 1, "tla": "NOR", "team": "McLaren", "team_color": "#ff8000"},
    {"position": 2, "tla": "PIA", "team": "McLaren", "team_color": "#ff8000"},
    {"position": 3, "tla": "LEC", "team": "Ferrari", "team_color": "#e8002d"},
    {"position": 4, "tla": "HAM", "team": "Ferrari", "team_color": "#e8002d"},
    {"position": 5, "tla": "VER", "team": "Red Bull", "team_color": "#1e2a62"},
    {"position": 6, "tla": "HAD", "team": "Red Bull", "team_color": "#1e2a62"},
    {"position": 7, "tla": "RUS", "team": "Mercedes", "team_color": "#00d2be"},
    {"position": 8, "tla": "ANT", "team": "Mercedes", "team_color": "#00d2be"},
    {"position": 9, "tla": "ALO", "team": "Aston Martin", "team_color": "#00665e"},
    {"position": 10, "tla": "STR", "team": "Aston Martin", "team_color": "#00665e"},
    {"position": 11, "tla": "GAS", "team": "Alpine", "team_color": "#0093cc"},
    {"position": 12, "tla": "COL", "team": "Alpine", "team_color": "#0093cc"},
    {"position": 13, "tla": "ALB", "team": "Williams", "team_color": "#1868db"},
    {"position": 14, "tla": "SAI", "team": "Williams", "team_color": "#1868db"},
    {"position": 15, "tla": "LAW", "team": "Racing Bulls", "team_color": "#6692ff"},
    {"position": 16, "tla": "LIN", "team": "Racing Bulls", "team_color": "#6692ff"},
    {"position": 17, "tla": "OCO", "team": "Haas", "team_color": "#b6babd"},
    {"position": 18, "tla": "BEA", "team": "Haas", "team_color": "#b6babd"},
    {"position": 19, "tla": "HUL", "team": "Audi", "team_color": "#bb0a30"},
    {"position": 20, "tla": "BOR", "team": "Audi", "team_color": "#bb0a30"},
    {"position": 21, "tla": "PER", "team": "Cadillac", "team_color": "#ececec"},
    {"position": 22, "tla": "BOT", "team": "Cadillac", "team_color": "#ececec"},
]

state_lock = threading.RLock()
stop_event = threading.Event()
connection_thread_started = False

state = {
    "session": {
        "name": "NO SESSION",
        "status": "INACTIVE",
        "phase": "",
        "time_remaining": "",
        "current_lap": "",
        "total_laps": "",
    },
    "track": {
        "status": "GREEN",
        "label": "",
        "sector": "",
    },
    "drivers": {},
    "connected": False,
    "auth_ok": False,
    "last_update": 0,
    "last_error": "",
}


def unwrap_value(value):
    if isinstance(value, dict):
        return value.get("Value", value.get("value", ""))
    return value


def clean_gap(value):
    value = unwrap_value(value)
    if value is None:
        return ""
    return str(value).replace("+", "").strip()


def normalize_team(team_name, tla, team_colour=None):
    if team_colour:
        colour = str(team_colour)
        if not colour.startswith("#"):
            colour = f"#{colour}"
        return team_name or "F1", colour

    team_name = (team_name or "").strip()

    for known, colour in TEAM_COLORS.items():
        if known.lower() in team_name.lower():
            return known, colour

    return team_name or "F1", "#9aa0a6"


def apply_patch(target, patch):
    if not isinstance(patch, dict):
        return

    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            apply_patch(target[key], value)
        else:
            target[key] = value


def topic_lines(data):
    if isinstance(data, dict) and isinstance(data.get("Lines"), dict):
        return data["Lines"]
    return data


def update_driver_list(data):
    data = topic_lines(data)

    if not isinstance(data, dict):
        return

    for number, info in data.items():
        if not isinstance(info, dict):
            continue

        driver = state["drivers"].setdefault(str(number), {})

        tla = info.get("Tla") or info.get("TLA") or driver.get("tla", "")
        team_name = (
            info.get("TeamName")
            or info.get("Team")
            or driver.get("team", "")
        )

        team, colour = normalize_team(
            team_name,
            tla,
            info.get("TeamColour"),
        )

        first = info.get("FirstName", "")
        last = info.get("LastName", "")

        driver.update({
            "number": str(number),
            "tla": tla,
            "team": team,
            "team_color": colour,
            "full_name": f"{first} {last}".strip(),
        })


def update_timing_data(data):
    data = topic_lines(data)

    if not isinstance(data, dict):
        return

    for number, info in data.items():
        if not isinstance(info, dict):
            continue

        driver = state["drivers"].setdefault(str(number), {})
        apply_patch(driver, info)

        driver["position"] = unwrap_value(
            driver.get("Position", driver.get("position", ""))
        )

        driver["interval"] = clean_gap(
            driver.get(
                "IntervalToPositionAhead",
                driver.get("Interval", driver.get("interval", "")),
            )
        )

        driver["gap"] = clean_gap(
            driver.get("GapToLeader", driver.get("gap", ""))
        )

        driver["lap"] = unwrap_value(
            driver.get("NumberOfLaps", driver.get("lap", ""))
        )

        in_pit = driver.get("InPit", driver.get("pit", False))

        driver["pit"] = str(unwrap_value(in_pit)).lower() in (
            "true", "1", "yes"
        )


def update_tyres(data):
    data = topic_lines(data)

    if not isinstance(data, dict):
        return

    for number, info in data.items():
        if not isinstance(info, dict):
            continue

        driver = state["drivers"].setdefault(str(number), {})
        stints = info.get("Stints", {})

        if isinstance(stints, dict) and stints:
            try:
                last_key = max(stints.keys(), key=lambda key: int(key))
            except (ValueError, TypeError):
                last_key = list(stints.keys())[-1]

            stint = stints.get(last_key, {})

        elif isinstance(stints, list) and stints:
            stint = stints[-1]

        else:
            stint = {}

        compound = (
            stint.get("Compound", "")
            if isinstance(stint, dict)
            else ""
        )

        if compound:
            driver["tyre"] = str(compound)[:1].upper()


def update_session_info(data):
    if not isinstance(data, dict):
        return

    session_name = data.get("Name") or data.get("Type") or ""

    name_map = {
        "Practice 1": "FP1",
        "Practice 2": "FP2",
        "Practice 3": "FP3",
        "Qualifying": "QUALI",
        "Qualifying 1": "Q1",
        "Qualifying 2": "Q2",
        "Qualifying 3": "Q3",
        "Race": "RACE",
        "Sprint": "SPRINT",
    }

    if session_name:
        state["session"]["name"] = name_map.get(
            session_name,
            str(session_name).upper(),
        )


def update_session_data(data):
    if not isinstance(data, dict):
        return

    status = data.get("Status")
    phase = data.get("Phase")
    remaining = unwrap_value(data.get("RemainingTime", ""))

    if status:
        state["session"]["status"] = str(status)

    if phase:
        state["session"]["phase"] = str(phase)

    if remaining:
        state["session"]["time_remaining"] = str(remaining)


def update_lap_count(data):
    if not isinstance(data, dict):
        return

    current_lap = data.get("CurrentLap", "")
    total_laps = data.get("TotalLaps", "")

    if current_lap not in ("", None):
        state["session"]["current_lap"] = current_lap

    if total_laps not in ("", None):
        state["session"]["total_laps"] = total_laps


def update_track_status(data):
    if not isinstance(data, dict):
        return

    status = str(data.get("Status", "1"))
    message = str(data.get("Message", "") or "")

    mapping = {
        "1": ("GREEN", "", ""),
        "2": ("YELLOW", "YELLOW FLAG", ""),
        "4": ("YELLOW", "SAFETY CAR", ""),
        "5": ("RED", "RED FLAG", ""),
        "6": ("YELLOW", "VSC", ""),
        "7": ("YELLOW", "VSC ENDING", ""),
    }

    track_status, label, sector = mapping.get(
        status,
        ("GREEN", message.upper(), ""),
    )

    lowered = message.lower()

    if "sector 1" in lowered:
        sector = "SECTOR 1"
    elif "sector 2" in lowered:
        sector = "SECTOR 2"
    elif "sector 3" in lowered:
        sector = "SECTOR 3"

    state["track"] = {
        "status": track_status,
        "label": label,
        "sector": sector,
    }


def handle_topic(topic, payload):
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return

    if topic == "DriverList":
        update_driver_list(payload)

    elif topic == "TimingData":
        update_timing_data(payload)

    elif topic == "TimingAppData":
        update_tyres(payload)

    elif topic == "SessionInfo":
        update_session_info(payload)

    elif topic == "SessionData":
        update_session_data(payload)

    elif topic == "SessionStatus":
        if isinstance(payload, dict):
            status = payload.get("Status", "")
        else:
            status = payload

        if status:
            state["session"]["status"] = str(status)

    elif topic == "TrackStatus":
        update_track_status(payload)

    elif topic == "LapCount":
        update_lap_count(payload)

    state["last_update"] = time.time()


def handle_feed_message(message):
    if isinstance(message, list):
        if len(message) >= 2 and isinstance(message[0], str):
            entries = [message]
        else:
            entries = message
    else:
        entries = [message]

    with state_lock:
        for entry in entries:
            if isinstance(entry, dict):
                topic = entry.get("topic") or entry.get("Category")
                payload = entry.get("data", entry.get("Message"))

            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                topic = entry[0]
                payload = entry[1]

            else:
                continue

            if isinstance(topic, str):
                handle_topic(topic, payload)


def snapshot():
    with state_lock:
        session = dict(state["session"])
        track = dict(state["track"])
        connected = bool(state["connected"])

        if connected and state["drivers"]:
            drivers = []

            for driver in state["drivers"].values():
                if not driver.get("tla"):
                    continue

                try:
                    position = int(
                        unwrap_value(driver.get("position", 999)) or 999
                    )
                except (TypeError, ValueError):
                    position = 999

                drivers.append({
                    "position": position,
                    "tla": driver.get("tla", ""),
                    "team": driver.get("team", ""),
                    "team_color": driver.get(
                        "team_color",
                        "#9aa0a6",
                    ),
                    "interval": driver.get("interval", ""),
                    "gap": driver.get("gap", ""),
                    "tyre": driver.get("tyre", ""),
                    "lap": driver.get("lap", ""),
                    "pit": driver.get("pit", False),
                    "status": driver.get("status", ""),
                    "knocked_out": bool(
                        driver.get("KnockedOut", False)
                    ),
                })

            drivers.sort(key=lambda item: item["position"])

            phase = str(session.get("phase", "")).upper()

            if phase in ("Q1", "Q2"):
                cutoff = 15 if phase == "Q1" else 10

                for driver in drivers:
                    if driver["position"] > cutoff:
                        driver["eliminated"] = True

            for driver in drivers:
                driver.setdefault(
                    "eliminated",
                    driver["knocked_out"],
                )

            return {
                "session": session,
                "track": track,
                "drivers": drivers,
                "server_time": datetime.now(
                    timezone.utc
                ).strftime("%H:%M:%S"),
                "connected": True,
            }

    fallback_drivers = [{
        "position": item["position"],
        "tla": item["tla"],
        "team": item["team"],
        "team_color": item["team_color"],
        "interval": "",
        "gap": "",
        "tyre": "",
        "lap": "",
        "pit": False,
        "status": "",
        "knocked_out": False,
        "eliminated": False,
    } for item in DEFAULT_DRIVERS]

    return {
        "session": session,
        "track": track,
        "drivers": fallback_drivers,
        "server_time": datetime.now(
            timezone.utc
        ).strftime("%H:%M:%S"),
        "connected": connected,
    }


def get_signalr_headers():
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Origin": "https://www.formula1.com",
    }

    response = requests.options(
        F1_NEGOTIATE_URL,
        headers=headers,
        timeout=20,
    )

    cookie = response.cookies.get("AWSALBCORS")

    if not cookie:
        raise RuntimeError(
            f"Cookie AWSALBCORS absent. "
            f"Réponse HTTP : {response.status_code}. "
            f"Cookies reçus : {list(response.cookies.keys())}"
        )

    headers["Cookie"] = f"AWSALBCORS={cookie}"

    return headers


def run_signalr_connection():
    headers = get_signalr_headers()

    def access_token_factory():
        token = get_token()

        with state_lock:
            state["auth_ok"] = True

        return token.value

    options = {
        "verify_ssl": True,
        "headers": headers,
        "access_token_factory": access_token_factory,
    }

    connected_event = threading.Event()
    closed_event = threading.Event()

    connection = (
        HubConnectionBuilder()
        .with_url(F1_SIGNALR_URL, options=options)
        .configure_logging(logging.WARNING)
        .build()
    )

    def on_open():
        logger.info("SignalR Core connection opened")

        with state_lock:
            state["connected"] = True
            state["last_error"] = ""

        connected_event.set()

    def on_close():
        logger.warning("SignalR Core connection closed")

        with state_lock:
            state["connected"] = False

        closed_event.set()

    def on_feed(message):
        handle_feed_message(message)

    connection.on_open(on_open)
    connection.on_close(on_close)
    connection.on("feed", on_feed)

    connection.start()

    if not connected_event.wait(timeout=30):
        raise TimeoutError(
            "SignalR connection did not open within 30 seconds"
        )

    connection.send("Subscribe", [TOPICS])

    while not stop_event.is_set() and not closed_event.wait(timeout=1):
        pass

    try:
        connection.stop()
    except Exception:
        logger.exception("Error while stopping SignalR connection")


def f1_connection_loop():
    while not stop_event.is_set():
        try:
            with state_lock:
                state["connected"] = False
                state["auth_ok"] = False

            logger.info("Connecting to F1 live timing...")

            run_signalr_connection()

        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"

            logger.exception("F1 live timing connection failed")

            with state_lock:
                state["connected"] = False
                state["last_error"] = message[:500]

        if not stop_event.is_set():
            time.sleep(10)


def start_f1_connection():
    global connection_thread_started

    with state_lock:
        if connection_thread_started:
            return

        connection_thread_started = True

    thread = threading.Thread(
        target=f1_connection_loop,
        name="f1-live-timing",
        daemon=True,
    )

    thread.start()


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/live")
def live():
    return jsonify(snapshot())


@app.route("/api/debug")
def debug():
    with state_lock:
        last_update = state["last_update"]

        return jsonify({
            "connected": state["connected"],
            "auth_ok": state["auth_ok"],
            "drivers": len(state["drivers"]),
            "session": state["session"],
            "track": state["track"],
            "last_update": last_update,
            "last_message_age_seconds": (
                round(time.time() - last_update, 1)
                if last_update else None
            ),
            "last_error": state["last_error"],
            "now": time.time(),
        })


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "connected": state["connected"],
    })


start_f1_connection()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
    )
