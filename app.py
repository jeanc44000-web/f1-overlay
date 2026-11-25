import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone

import requests
import websockets
from flask import Flask, Response, render_template

app = Flask(__name__)

F1_NEGOTIATE_URL = (
    "https://livetiming.formula1.com/signalrcore/negotiate?negotiateVersion=1"
)

F1_HUB_URL = "wss://livetiming.formula1.com/signalrcore"

F1_API_KEY = os.getenv(
    "F1_API_KEY",
    "JJXYS7ilZM7FYIGDXIN3tc2aTqTIaFqd",
)

OPENF1_URL = "https://api.openf1.org/v1"

TOPICS = [
    "Heartbeat",
    "SessionInfo",
    "SessionData",
    "SessionStatus",
    "DriverList",
    "TimingData",
    "TimingAppData",
    "TrackStatus",
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

FALLBACK_TEAM_BY_TLA = {
    "NOR": "McLaren",
    "PIA": "McLaren",
    "LEC": "Ferrari",
    "HAM": "Ferrari",
    "VER": "Red Bull",
    "HAD": "Red Bull",
    "RUS": "Mercedes",
    "ANT": "Mercedes",
    "ALO": "Aston Martin",
    "STR": "Aston Martin",
    "GAS": "Alpine",
    "COL": "Alpine",
    "ALB": "Williams",
    "SAI": "Williams",
    "LAW": "Racing Bulls",
    "TSU": "Racing Bulls",
    "OCO": "Haas",
    "CAM": "Haas",
    "HUL": "Audi",
    "BOR": "Audi",
    "PER": "Cadillac",
    "BOT": "Cadillac",
}

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


def normalize_team(team_name, tla):
    team_name = (team_name or "").strip()

    for known, color in TEAM_COLORS.items():
        if known.lower() in team_name.lower():
            return known, color

    fallback = FALLBACK_TEAM_BY_TLA.get(tla)

    if fallback:
        return fallback, TEAM_COLORS[fallback]

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

        team, color = normalize_team(team_name, tla)

        driver.update({
            "number": str(number),
            "tla": tla,
            "team": team,
            "team_color": info.get("TeamColour") or color,
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

    state["last_update"] = time.time()


def snapshot():
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
        "connected": state["connected"],
    }


def build_headers():
    token = os.getenv("F1TV_TOKEN", "").strip()

    headers = {
        "apikey": F1_API_KEY,
        "CD-SystemID": "60a9ad84-e93d-480f-80d6-af37494f2e22",
        "CD-DeviceType": "4",
        "CD-DistributionChannel": "95d6cc7d-63fe-453b-b734-1cde4fa3d08c",
        "CD-Language": "en-US",
        "Origin": "https://f1tv.formula1.com",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    return headers


async def signalr_loop():
    while True:
        try:
            headers = build_headers()

            response = requests.post(
                F1_NEGOTIATE_URL,
                headers=headers,
                timeout=20,
            )

            if response.status_code != 200:
                state["connected"] = False
                state["last_error"] = (
                    f"Negotiate failed: {response.status_code}"
                )
                await asyncio.sleep(10)
                continue

            negotiation = response.json()

            connection_id = negotiation["connectionId"]
            access_token = negotiation.get("accessToken", "")

            websocket_headers = dict(headers)

            if access_token:
                websocket_headers["Authorization"] = (
                    f"Bearer {access_token}"
                )

            websocket_url = (
                f"{F1_HUB_URL}?id={connection_id}&negotiateVersion=1"
            )

            async with websockets.connect(
                websocket_url,
                extra_headers=websocket_headers,
                ping_interval=10,
                ping_timeout=20,
            ) as websocket:
                handshake = json.dumps({
                    "protocol": "json",
                    "version": 1,
                }) + "\x1e"

                await websocket.send(handshake)

                subscribe = json.dumps({
                    "arguments": [TOPICS],
                    "target": "Subscribe",
                    "type": 1,
                }) + "\x1e"

                await websocket.send(subscribe)

                state["connected"] = True
                state["last_error"] = ""

                async for raw in websocket:
                    for message in raw.split("\x1e"):
                        if not message:
                            continue

                        try:
                            decoded = json.loads(message)
                        except json.JSONDecodeError:
                            continue

                        if decoded.get("type") != 1:
                            continue

                        if decoded.get("target") != "Streaming":
                            continue

                        arguments = decoded.get("arguments", [])

                        if len(arguments) < 2:
                            continue

                        topic = arguments[0]
                        payload = arguments[1]

                        if isinstance(payload, str):
                            try:
                                payload = json.loads(payload)
                            except json.JSONDecodeError:
                                continue

                        handle_topic(topic, payload)

        except Exception as error:
            state["connected"] = False
            state["last_error"] = str(error)
            await asyncio.sleep(10)


def start_background_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(signalr_loop())


threading.Thread(target=start_background_loop, daemon=True).start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stream")
def stream():
    def generate():
        last_payload = None

        while True:
            current = snapshot()
            encoded = json.dumps(current, separators=(",", ":"))

            if encoded != last_payload:
                yield f"data: {encoded}\n\n"
                last_payload = encoded

            time.sleep(0.25)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/last-result")
def last_result():
    try:
        sessions = requests.get(
            f"{OPENF1_URL}/sessions?session_name=Race",
            timeout=20,
        ).json()

        sessions = [
            session
            for session in sessions
            if session.get("date_end")
        ]

        sessions.sort(
            key=lambda session: session["date_end"],
            reverse=True,
        )

        if not sessions:
            return {"session": {"name": "LAST RACE"}, "drivers": []}

        last_session = sessions[0]
        session_key = last_session["session_key"]

        positions = requests.get(
            f"{OPENF1_URL}/position?session_key={session_key}&position<=20",
            timeout=20,
        ).json()

        drivers = requests.get(
            f"{OPENF1_URL}/drivers?session_key={session_key}",
            timeout=20,
        ).json()

        driver_map = {
            driver["driver_number"]: driver
            for driver in drivers
        }

        result = []

        for item in positions:
            number = item["driver_number"]
            info = driver_map.get(number, {})

            tla = info.get("name_acronym", "")
            team, color = normalize_team(
                info.get("team_name", ""),
                tla,
            )

            result.append({
                "position": item["position"],
                "tla": tla,
                "team": team,
                "team_color": color,
                "interval": "",
                "gap": "",
                "tyre": "",
                "eliminated": False,
            })

        result.sort(key=lambda driver: driver["position"])

        return {
            "session": {
                "name": "LAST RACE",
                "status": "FINISHED",
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
            "drivers": result,
            "connected": True,
        }

    except Exception as error:
        return {
            "session": {"name": "LAST RACE"},
            "track": {"status": "GREEN"},
            "drivers": [],
            "error": str(error),
        }


@app.route("/api/debug")
def debug():
    return {
        "connected": state["connected"],
        "drivers": len(state["drivers"]),
        "session": state["session"],
        "track": state["track"],
        "last_update": state["last_update"],
        "last_error": state["last_error"],
        "token_present": bool(os.getenv("F1TV_TOKEN", "").strip()),
        "now": time.time(),
    }


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
    )
