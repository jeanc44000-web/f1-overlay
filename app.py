import os
import time
import requests
from flask import Flask, jsonify, render_template

app = Flask(__name__)

OPENF1_USER = os.getenv("OPENF1_USER")
OPENF1_PASS = os.getenv("OPENF1_PASS")

TOKEN_URL = "https://api.openf1.org/token"
API_BASE = "https://api.openf1.org/v1"

_cached_token = None
_token_exp = 0


def get_token():
    global _cached_token, _token_exp

    if _cached_token and time.time() < _token_exp - 60:
        return _cached_token

    if not OPENF1_USER or not OPENF1_PASS:
        raise RuntimeError("Missing OPENF1_USER or OPENF1_PASS")

    response = requests.post(
        TOKEN_URL,
        data={
            "username": OPENF1_USER,
            "password": OPENF1_PASS
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        timeout=20
    )
    response.raise_for_status()
    token_data = response.json()

    _cached_token = token_data["access_token"]
    _token_exp = time.time() + int(token_data.get("expires_in", 3600))
    return _cached_token


def api_get(path, params=None):
    token = get_token()
    response = requests.get(
        f"{API_BASE}{path}",
        params=params or {},
        headers={
            "accept": "application/json",
            "Authorization": f"Bearer {token}"
        },
        timeout=20
    )
    response.raise_for_status()
    return response.json()


def fmt_time(seconds):
    if seconds is None:
        return "—"
    try:
        seconds = float(seconds)
    except Exception:
        return "—"
    m = int(seconds // 60)
    s = seconds - (m * 60)
    if m > 0:
        return f"{m}:{s:06.3f}"
    return f"{s:0.3f}"


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/current")
def current():
    try:
        sessions = api_get("/sessions", {"year": 2026})

        if not isinstance(sessions, list) or not sessions:
            return jsonify({
                "ok": True,
                "meeting": "OpenF1",
                "session": "No session found",
                "rows": []
            })

        session = sessions[-1]
        session_key = session.get("session_key")
        meeting_key = session.get("meeting_key")

        if not session_key or not meeting_key:
            return jsonify({
                "ok": True,
                "meeting": "OpenF1",
                "session": "No active session",
                "rows": []
            })

        drivers = api_get("/drivers", {"session_key": session_key})
        laps = api_get("/laps", {"session_key": session_key})
        session_info = api_get("/sessions", {"session_key": session_key})
        meeting_info = api_get("/meetings", {"meeting_key": meeting_key})

        session_info = session_info[0] if isinstance(session_info, list) and session_info else {}
        meeting_info = meeting_info[0] if isinstance(meeting_info, list) and meeting_info else {}

        name_by_num = {}
        team_by_num = {}

        for d in drivers if isinstance(drivers, list) else []:
            num = str(d.get("driver_number", ""))
            if num:
                name_by_num[num] = (
                    d.get("broadcast_name")
                    or d.get("full_name")
                    or d.get("driver_name")
                    or f"#{num}"
                )
                team_by_num[num] = d.get("team_name") or "Reserve"

        best_laps = {}

        for lap in laps if isinstance(laps, list) else []:
            num = str(lap.get("driver_number", ""))
            raw_time = lap.get("lap_duration") or lap.get("duration") or lap.get("lap_time")

            try:
                lap_time = float(raw_time)
            except Exception:
                continue

            if not num:
                continue

            if num not in best_laps or lap_time < best_laps[num]:
                best_laps[num] = lap_time

        team_colors = {
            "Mercedes": "#00d2be",
            "Ferrari": "#dc0000",
            "McLaren": "#ff8700",
            "Red Bull Racing": "#3671c6",
            "Racing Bulls": "#6692ff",
            "Alpine": "#0090ff",
            "Haas": "#b6babd",
            "Williams": "#64c4ff",
            "Aston Martin": "#006f62",
            "Sauber": "#00a19b",
            "Kick Sauber": "#00a19b",
            "Reserve": "#7a7a7a"
        }

        rows = []
        for num, lap_time in best_laps.items():
            team = team_by_num.get(num, "Reserve")
            rows.append({
                "pos": None,
                "driver": name_by_num.get(num, f"#{num}"),
                "team": team,
                "color": team_colors.get(team, "#7a7a7a"),
                "time": lap_time,
                "time_text": fmt_time(lap_time),
                "gap": None,
                "gap_text": "—"
            })

        rows.sort(key=lambda x: x["time"])

        if rows:
            leader = rows[0]["time"]
            for i, row in enumerate(rows, start=1):
                row["pos"] = i
                gap = max(0.0, row["time"] - leader)
                row["gap"] = gap
                row["gap_text"] = "+0.000" if i == 1 else f"+{gap:0.3f}"

        return jsonify({
            "ok": True,
            "meeting": meeting_info.get("meeting_name") or "OpenF1",
            "session": session_info.get("session_name") or "Session",
            "rows": rows
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "rows": []
        })


if __name__ == "__main__":
    app.run()
