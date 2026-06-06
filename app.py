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

    if not OPENF1_USER or not OPENF1_PASS:
        raise RuntimeError("Missing OPENF1_USER or OPENF1_PASS")

    r = requests.post(
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
            "Authorization": f"Bearer {token}"
        },
        timeout=20
    )
    r.raise_for_status()
    return r.json()


@app.route("/")
def home():
    return jsonify({"ok": True, "message": "OpenF1 backend is running"})


@app.route("/api/data")
def data():
    try:
        sessions = api_get("/sessions", {"year": 2026})

        if not isinstance(sessions, list) or not sessions:
            return jsonify({
                "ok": False,
                "error": "No sessions found",
                "sessions": [],
                "drivers": [],
                "laps": []
            })

        session = sorted(
            sessions,
            key=lambda s: (
                s.get("date_start") or "",
                s.get("date_end") or "",
                str(s.get("session_key") or "")
            )
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
        return jsonify({
            "ok": False,
            "error": str(e),
            "sessions": [],
            "drivers": [],
            "laps": []
        })


if __name__ == "__main__":
    app.run()
