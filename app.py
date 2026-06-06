import os
import time
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, jsonify, make_response

app = Flask(__name__)

OPENF1_USER = os.getenv("OPENF1_USER")
OPENF1_PASS = os.getenv("OPENF1_PASS")

TOKEN_URL = "https://api.openf1.org/token"
API_BASE = "https://api.openf1.org/v1"

_token = None
_token_exp = 0
_cache = {"ts": 0, "data": None}


def cors_json(payload, status=200):
    resp = make_response(jsonify(payload), status)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp


def get_token():
    global _token, _token_exp
    if _token and time.time() < _token_exp - 60:
        return _token
    if not OPENF1_USER or not OPENF1_PASS:
        raise RuntimeError("Missing OPENF1_USER or OPENF1_PASS")

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
    if r.status_code == 404:
        return []
    if r.status_code == 429:
        raise RuntimeError("OpenF1 rate limit hit")
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return []


def fmt_gap(x):
    if x is None or x == "":
        return ""
    if isinstance(x, (int, float)):
        return f"+{x:.3f}" if x >= 0 else f"{x:.3f}"
    s = str(x).strip()
    return s if s.startswith("+") or s.startswith("-") else f"+{s}"


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def sessions_for_year(year=2026):
    sessions = api_get("/sessions", {"year": year})
    return sessions if isinstance(sessions, list) else []


def pick_session():
    sessions = sessions_for_year(2026)
    if not sessions:
        return None

    now = datetime.now(timezone.utc)
    allowed = {"FP1", "FP2", "FP3", "Qualifying", "Sprint Qualifying", "Sprint", "Race"}

    candidates = []
    for s in sessions:
        start = parse_dt(s.get("date_start"))
        if not start:
            continue
        name = (s.get("session_name") or "").strip()
        if name not in allowed:
            continue
        live_start = start - timedelta(minutes=60)
        end = parse_dt(s.get("date_end"))
        live_end = (end + timedelta(minutes=60)) if end else (start + timedelta(hours=6))
        if live_start <= now <= live_end:
            candidates.append(s)

    if candidates:
        candidates.sort(key=lambda s: (s.get("date_start") or "", s.get("date_end") or "", str(s.get("session_key") or "")))
        return candidates[-1]

    future = []
    for s in sessions:
        start = parse_dt(s.get("date_start"))
        if not start:
            continue
        if start >= now - timedelta(hours=6):
            future.append(s)

    if future:
        future.sort(key=lambda s: parse_dt(s.get("date_start")) or datetime.max.replace(tzinfo=timezone.utc))
        return future[0]

    return None


def fetch_live_rows(session_key):
    drivers = api_get("/drivers", {"session_key": session_key})
    positions = api_get("/position", {"session_key": session_key})
    intervals = api_get("/intervals", {"session_key": session_key})

    driver_map = {}
    for d in drivers if isinstance(drivers, list) else []:
        dn = d.get("driver_number")
        if dn is not None:
            driver_map[int(dn)] = d

    latest_pos = {}
    for p in positions if isinstance(positions, list) else []:
        dn = p.get("driver_number")
        if dn is None:
            continue
        latest_pos[int(dn)] = p

    latest_interval = {}
    for i in intervals if isinstance(intervals, list) else []:
        dn = i.get("driver_number")
        if dn is None:
            continue
        latest_interval[int(dn)] = i

    rows = []
    for dn, p in latest_pos.items():
        d = driver_map.get(dn, {})
        iv = latest_interval.get(dn, {})

        gap = iv.get("gap_to_leader")
        if gap in (None, ""):
            gap = iv.get("interval_to_position_ahead")
        if gap in (None, ""):
            gap = iv.get("interval")
        if gap in (None, "") and p.get("position") not in (None, 1):
            gap = ""

        rows.append({
            "position": p.get("position"),
            "last_name": d.get("last_name") or d.get("broadcast_name") or "",
            "team_name": d.get("team_name") or "",
            "team_color": d.get("team_colour") or d.get("team_color") or "",
            "gap": fmt_gap(gap),
        })

    rows.sort(key=lambda x: x["position"] if x["position"] is not None else 999)
    return rows


@app.route("/")
def home():
    return cors_json({"ok": True, "message": "OpenF1 overlay backend", "endpoint": "/api/data"})


@app.route("/api/data", methods=["GET", "OPTIONS"])
def data():
    now = time.time()
    tick = int(now // 5) % 3

    if _cache["data"] is not None and now - _cache["ts"] < 5:
        return cors_json(_cache["data"])

    try:
        session = pick_session()

        if not session:
            payload = {
                "ok": False,
                "error": "aucune séance en cours",
                "session": "",
                "tick": tick,
                "rows": [],
            }
            _cache["ts"] = now
            _cache["data"] = payload
            return cors_json(payload)

        session_key = session.get("session_key")
        rows = fetch_live_rows(session_key)

        payload = {
            "ok": True,
            "session": (session.get("session_name") or "SESSION").upper(),
            "session_key": session_key,
            "tick": tick,
            "rows": rows,
        }
        _cache["ts"] = now
        _cache["data"] = payload
        return cors_json(payload)

    except Exception as e:
        payload = {
            "ok": False,
            "error": str(e),
            "session": "",
            "tick": tick,
            "rows": [],
        }
        _cache["ts"] = now
        _cache["data"] = payload
        return cors_json(payload)


@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
