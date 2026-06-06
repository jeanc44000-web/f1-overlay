import os
import time
import requests
from flask import Flask, jsonify

app = Flask(__name__)

OPENF1_USER = os.getenv("OPENF1_USER")
OPENF1_PASS = os.getenv("OPENF1_PASS")

TOKEN_URL = "https://api.openf1.org/token"
API_BASE = "https://api.openf1.org/v1"

_token = None
_token_exp = 0

def get_token():
    global _token, _token_exp
    if _token and time.time() < _token_exp - 60:
        return _token

    r = requests.post(
        TOKEN_URL,
        data={"username": OPENF1_USER, "password": OPENF1_PASS},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    _token = data["access_token"]
    _token_exp = time.time() + int(data.get("expires_in", 3600))
    return _token

def api_get(path, params=None):
    token = get_token()
    r = requests.get(
        f"{API_BASE}{path}",
        params=params or {},
        headers={
            "accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
        timeout=20,
    )
    r.raise_for_status()
    return r.json()

@app.route("/api/data")
def data():
    try:
        sessions = api_get("/sessions", {"year": 2026})
        if not isinstance(sessions, list) or not sessions:
            return jsonify({"ok": False, "error": "No sessions found", "drivers": [], "laps": []})

        session = sorted(
            sessions,
            key=lambda s: (s.get("date_start") or "", s.get("date_end") or "", str(s.get("session_key") or ""))
        )[-1]

        session_key = session.get("session_key")
        drivers = api_get("/drivers", {"session_key": session_key})
        laps = api_get("/laps", {"session_key": session_key})

        return jsonify({
            "ok": True,
            "session": session.get("session_name"),
            "meeting": session.get("meeting_key"),
            "session_key": session_key,
            "drivers": drivers,
            "laps": laps
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "drivers": [], "laps": []})

if __name__ == "__main__":
    app.run()

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
            "Audi": "#00a19b",
            "Cadillace": "#7a7a7a",
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
                "gap_text": "—",
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
            "session": session.get("session_name") or "Session",
            "rows": rows
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "rows": []
        })
