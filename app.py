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
    <title>OpenF1 positions</title>
    <style>
      body { font-family: Arial, sans-serif; padding: 20px; background: #111; color: #eee; }
      h1 { margin-bottom: 6px; }
      .meta { color: #aaa; margin-bottom: 16px; }
      table { border-collapse: collapse; width: 100%; max-width: 700px; }
      th, td { padding: 10px; border-bottom: 1px solid #333; text-align: left; }
      th { background: #222; }
      .gap { color: #ddd; text-align: right; }
      .pos { width: 60px; }
      .name { width: 220px; }
    </style>
  </head>
  <body>
    <h1>OpenF1 positions</h1>
    <div class="meta" id="meta">Loading...</div>
    <table>
      <thead>
        <tr>
          <th class="pos">Pos</th>
          <th class="name">Pilote</th>
          <th class="gap">Gap</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>

    <script>
      function formatGap(v) {
        if (v === null || v === undefined || v === "") return "";
        return String(v);
      }

      async function loadData() {
        const res = await fetch('/api/data');
        const data = await res.json();

        document.getElementById('meta').textContent =
          data.ok ? `${data.session} (${data.year}) — ${data.rows.length} drivers` : `Error: ${data.error}`;

        const rows = document.getElementById('rows');
        rows.innerHTML = '';

        (data.rows || []).forEach(r => {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td class="pos">${r.position ?? ''}</td>
            <td class="name">${r.first_name ?? ''} ${r.last_name ?? ''}</td>
            <td class="gap">${formatGap(r.gap)}</td>
          `;
          rows.appendChild(tr);
        });
      }

      loadData();
      setInterval(loadData, 4000);
    </script>
  </body>
</html>
"""

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
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return []

def pick_latest_session():
    for year in [2026, 2025, 2024]:
        sessions = api_get("/sessions", {"year": year})
        if isinstance(sessions, list) and sessions:
            session = sorted(
                sessions,
                key=lambda s: (
                    s.get("date_start") or "",
                    s.get("date_end") or "",
                    str(s.get("session_key") or "")
                )
            )[-1]
            return year, session
    return None, None

@app.route("/")
def home():
    return render_template_string(HTML)

@app.route("/api/data")
def data():
    try:
        year, session = pick_latest_session()
        if not session:
            return jsonify({"ok": False, "error": "No sessions found", "rows": []})

        session_key = session.get("session_key")
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
            rows.append({
                "position": p.get("position"),
                "driver_number": dn,
                "first_name": d.get("first_name") or "",
                "last_name": d.get("last_name") or "",
                "gap": iv.get("gap_to_leader") or iv.get("interval") or iv.get("gap") or ""
            })

        rows = sorted(rows, key=lambda x: x["position"] if x["position"] is not None else 999)

        return jsonify({
            "ok": True,
            "year": year,
            "session": session.get("session_name"),
            "session_key": session_key,
            "rows": rows
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "rows": []})

if __name__ == "__main__":
    app.run()
