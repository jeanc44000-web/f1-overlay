import os
import time
import requests
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

OPENF1_USER = os.getenv("OPENF1_USER")
OPENF1_PASS = os.getenv("OPENF1_PASS")

TOKEN_URL = "https://api.openf1.org/token"
API_BASE = "https://api.openf1.org/v1"

_token = None
_token_exp = 0

HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>OpenF1 Drivers</title>
    <style>
      body { font-family: Arial, sans-serif; padding: 20px; background: #111; color: #eee; }
      table { border-collapse: collapse; width: 100%; }
      th, td { padding: 10px; border-bottom: 1px solid #333; text-align: left; }
      th { background: #222; }
      .team { color: #aaa; }
    </style>
  </head>
  <body>
    <h1>OpenF1 drivers</h1>
    <p id="meta">Loading...</p>
    <table>
      <thead>
        <tr>
          <th>Number</th>
          <th>Driver</th>
          <th>Team</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
    <script>
      async function loadData() {
        const res = await fetch('/api/data');
        const data = await res.json();
        document.getElementById('meta').textContent =
          data.ok ? `${data.session} (${data.year}) — ${data.drivers_count} drivers` : `Error: ${data.error}`;

        const rows = document.getElementById('rows');
        rows.innerHTML = '';
        (data.drivers || []).forEach(d => {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${d.driver_number ?? ''}</td><td>${d.full_name ?? ''}</td><td class="team">${d.team_name ?? ''}</td>`;
          rows.appendChild(tr);
        });
      }
      loadData();
    </script>
  </body>
</html>
"""

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
    if r.status_code == 404:
        return []
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return []

@app.route("/")
def home():
    return render_template_string(HTML)

@app.route("/api/data")
def data():
    try:
        sessions = api_get("/sessions", {"year": 2026})
        if not isinstance(sessions, list) or not sessions:
            return jsonify({"ok": False, "error": "No sessions found", "drivers": []})

        session = sorted(
            sessions,
            key=lambda s: (s.get("date_start") or "", s.get("date_end") or "", str(s.get("session_key") or ""))
        )[-1]

        session_key = session.get("session_key")
        drivers = api_get("/drivers", {"session_key": session_key})

        return jsonify({
            "ok": True,
            "session": session.get("session_name"),
            "year": 2026,
            "session_key": session_key,
            "drivers_count": len(drivers) if isinstance(drivers, list) else 0,
            "drivers": drivers if isinstance(drivers, list) else []
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "drivers": []
        })
