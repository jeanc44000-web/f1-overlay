import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests
import websockets
from flask import Flask, Response, render_template

app = Flask(__name__)

F1_NEGOTIATE_URL = (
    "https://livetiming.formula1.com/signalr/negotiate"
    "?connectionData=%5B%7B%22name%22%3A%22Streaming%22%7D%5D"
    "&clientProtocol=1.5"
)

F1_CONNECT_URL = (
    "wss://livetiming.formula1.com/signalr/connect"
    "?clientProtocol=1.5"
    "&transport=webSockets"
    "&connectionToken={token}"
    "&connectionData=%5B%7B%22name%22%3A%22Streaming%22%7D%5D"
)

TOPICS = [
    "Heartbeat",
    "SessionInfo",
    "SessionData",
    "SessionStatus",
    "DriverList",
    "TimingData",
    "TimingAppData",
    "TrackStatus",
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
    {"position": 16, "tla": "TSU", "team": "Racing Bulls", "team_color": "#6692ff"},
    {"position": 17, "tla": "OCO", "team": "Haas", "team_color": "#b6babd"},
    {"position": 18, "tla": "CAM", "team": "Haas", "team_color": "#b6babd"},
    {"position": 19, "tla": "HUL", "team": "Audi", "team_color": "#bb0a30"},
    {"position": 20, "tla": "BOR", "team": "Audi", "team_color": "#bb0a30"},
    {"position": 21, "tla": "PER", "team": "Cadillac", "team_color": "#ececec"},
    {"position": 22, "tla": "BOT", "team": "Cadillac", "team_color": "#ececec"},
]

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
    "last_update": 0,
    "last_error": "",
}


def normalize_team(team_name, tla, team_colour=None):
    if team_colour:
        return team_name or "F1", team_colour

    team_name = (team_name or "").strip()

    for known, color in TEAM_COLORS.items():
        if known.lower() in team_name.lower():
            return known, color

    for driver in DEFAULT_DRIVERS:
        if driver["tla"] == tla:
            return driver["team"], driver["team_color"]

    return team_name or "F1", "#9aa0a6"


def clean_gap(value):
    if not value:
        return ""

    return str(value).replace("+", "").strip()


def apply_patch(target, patch):
    if not isinstance(patch, dict):
        return

    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            apply_patch(target[key], value)
        else:
            target[key] = value


def update_driver_list(data):
    if not isinstance(data, dict):
        return

    for number, info in data.items():
        driver = state["drivers"].setdefault(str(number), {})

        tla = info.get("Tla") or info.get("TLA") or driver.get("tla", "")
        team_name = (
            info.get("TeamName")
            or info.get("Team")
            or driver.get("team", "")
        )

        team, color = normalize_team(
            team_name,
            tla,
            info.get("TeamColour"),
        )

        driver.update({
            "number": str(number),
            "tla": tla,
            "team": team,
            "team_color": color,
            "full_name": (
                f"{info.get('FirstName', '')} "
                f"{info.get('LastName', '')}"
            ),
        })


def update_timing_data(data):
    if not isinstance(data, dict):
        return

    for number, info in data.items():
        driver = state["drivers"].setdefault(str(number), {})
        apply_patch(driver, info)

        driver["position"] = driver.get(
            "Position",
            driver.get("position", ""),
        )

        driver["interval"] = clean_gap(
            driver.get("Interval", driver.get("interval", ""))
        )

        driver["gap"] = clean_gap(
            driver.get("GapToLeader", driver.get("gap", ""))
        )

        driver["lap"] = driver.get(
            "NumberOfLaps",
            driver.get("lap", ""),
        )

        driver["pit"] = bool(
            driver.get("InPit", driver.get("pit", False))
        )


def update_tyres(data):
    if not isinstance(data, dict):
        return

    for number, info in data.items():
        driver = state["drivers"].setdefault(str(number), {})
        stints = info.get("Stints")

        if isinstance(stints, list) and stints:
            compound = stints[-1].get("Compound", "")

            if compound:
                driver["tyre"] = compound[:1].upper()


def update_session_info(data):
    if not isinstance(data, dict):
        return

    session_name = data.get("Name", "")

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

    state["session"]["name"] = (
        name_map.get(session_name, session_name.upper()) or "NO SESSION"
    )


def update_session_data(data):
    if not isinstance(data, dict):
        return

    state["session"]["status"] = (
        data.get("Status", "") or state["session"]["status"]
    )

    state["session"]["phase"] = (
        data.get("Phase", "") or state["session"]["phase"]
    )

    remaining = data.get("RemainingTime") or ""

    if remaining:
        state["session"]["time_remaining"] = remaining


def update_lap_count(data):
    if not isinstance(data, dict):
        return

    current_lap = data.get("CurrentLap", "")
    total_laps = data.get("TotalLaps", "")

    if current_lap:
        state["session"]["current_lap"] = current_lap

    if total_laps:
        state["session"]["total_laps"] = total_laps


def update_track_status(data):
    if not isinstance(data, dict):
        return

    status = str(data.get("Status", "1"))
    message = data.get("Message", "") or ""

    mapping = {
        "1": ("GREEN", "", ""),
        "2": ("YELLOW", "YELLOW FLAG", ""),
        "4": ("YELLOW", "SAFETY CAR", ""),
        "6": ("YELLOW", "VSC", ""),
        "7": ("RED", "RED FLAG", ""),
    }

    track_status, label, sector = mapping.get(
        status,
        ("GREEN", message.upper(), ""),
    )

    if "Sector 1" in message:
        sector = "SECTOR 1"
    elif "Sector 2" in message:
        sector = "SECTOR 2"
    elif "Sector 3" in message:
        sector = "SECTOR 3"

    state["track"] = {
        "status": track_status,
        "label": label,
        "sector": sector,
    }


def handle_topic(topic, payload):
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
        state["session"]["status"] = str(payload)

    elif topic == "TrackStatus":
        update_track_status(payload)

    elif topic == "LapCount":
        update_lap_count(payload)

    state["last_update"] = time.time()


def snapshot():
    if state["connected"] and state["drivers"]:
        drivers = []

        for driver in state["drivers"].values():
            if not driver.get("tla"):
                continue

            try:
                position = int(driver.get("position", 999))
            except (TypeError, ValueError):
                position = 999

            drivers.append({
                "position": position,
                "tla": driver.get("tla", ""),
                "team": driver.get("team", ""),
                "team_color": driver.get("team_color", "#9aa0a6"),
                "interval": driver.get("interval", ""),
                "gap": driver.get("gap", ""),
                "tyre": driver.get("tyre", ""),
                "lap": driver.get("lap", ""),
                "pit": driver.get("pit", False),
                "status": driver.get("status", ""),
                "knocked_out": bool(driver.get("KnockedOut", False)),
            })

        drivers.sort(key=lambda driver: driver["position"])

        phase = state["session"]["phase"].upper()

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
            "session": state["session"],
            "track": state["track"],
            "drivers": drivers,
            "server_time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "connected": True,
        }

    fallback_drivers = []

    for driver in DEFAULT_DRIVERS:
        fallback_drivers.append({
            "position": driver["position"],
            "tla": driver["tla"],
            "team": driver["team"],
            "team_color": driver["team_color"],
            "interval": "",
            "gap": "",
            "tyre": "",
            "lap": "",
            "pit": False,
            "status": "",
            "knocked_out": False,
            "eliminated": False,
        })

    return {
        "session": state["session"],
        "track": state["track"],
        "drivers": fallback_drivers,
        "server_time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "connected": state["connected"],
    }


def negotiate():
    headers = {
        "User-Agent": "BestHTTP",
        "Accept-Encoding": "gzip,identity",
    }

    response = requests.get(
        F1_NEGOTIATE_URL,
        headers=headers,
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()
    cookies = response.headers.get("Set-Cookie", "")

    return data["ConnectionToken"], cookies


async def f1_live_loop():
    while True:
        try:
            connection_token, cookie = negotiate()

            websocket_url = F1_CONNECT_URL.format(
                token=quote(connection_token, safe="")
            )

            headers = {
                "User-Agent": "BestHTTP",
                "Accept-Encoding": "gzip,identity",
                "Cookie": cookie,
            }

            async with websockets.connect(
                websocket_url,
                extra_headers=headers,
                ping_interval=None,
                ping_timeout=None,
            ) as websocket:
                subscribe_message = json.dumps({
                    "H": "Streaming",
                    "M": "Subscribe",
                    "A": [TOPICS],
                    "I": 1,
                })

                await websocket.send(subscribe_message)

                state["connected"] = True
                state["last_error"] = ""

                async for raw_message in websocket:
                    try:
                        message = json.loads(raw_message)
                    except json.JSONDecodeError:
                        continue

                    method_calls = message.get("M", [])

                    for method_call in method_calls:
                        if method_call.get("H") != "Streaming":
                            continue

                        topic = method_call.get("M", "")
                        arguments = method_call.get("A", [])

                        if not topic or not arguments:
                            continue

                        payload = arguments[0]

                        if isinstance(payload, str):
                            try:
                                payload = json.loads(payload)
                            except json.JSONDecodeError:
                                continue

                        handle_topic(topic, payload)

        except Exception as error:
            state["connected"] = False
            state["last_error"] = str(error)
            await asyncio.sleep(5)


def start_f1_connection():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(f1_live_loop())


threading.Thread(target=start_f1_connection, daemon=True).start()


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
    return snapshot()


@app.route("/api/debug")
def debug():
    return {
        "connected": state["connected"],
        "drivers": len(state["drivers"]),
        "session": state["session"],
        "track": state["track"],
        "last_update": state["last_update"],
        "last_error": state["last_error"],
        "now": time.time(),
    }


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
    )
