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
      .gap { text-align: right; }
    </style>
  </head>
  <body>
    <h1>OpenF1 positions</h1>
    <div class="meta" id="meta">Loading...</div>
    <table>
      <thead>
        <tr>
          <th>Pos</th>
          <th>Pilote</th>
          <th>Gap</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>

    <script>
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
            <td>${r.position ?? ''}</td>
            <td>${r.first_name ?? ''}</td>
            <td class="gap">${r.gap ?? ''}</td>
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

def fmt_gap(x):
    if x is None or x == "":
        return ""
    if isinstance(x, (int, float)):
        return f"+{x:.3f}" if x >= 0 else f"{x:.3f}"
    s = str(x)
    return s if s.startswith("+") or s.startswith("-") else f"+{s}"

@app.route("/")
def home():
    return render_template_string(HTML)

@app.route("/api/data")
def data():
    try:
        drivers = api_get("/drivers", {"session_key": "latest"})
        positions = api_get("/position", {"session_key": "latest"})
        intervals = api_get("/intervals", {"session_key": "latest"})
        sessions = api_get("/sessions", {"session_key": "latest"})

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
            gap = iv.get("gap_to_leader") or iv.get("interval") or ""
            rows.append({
                "position": p.get("position"),
                "first_name": d.get("first_name") or d.get("broadcast_name") or "",
                "gap": fmt_gap(gap)
            })

        rows = sorted(rows, key=lambda x: x["position"] if x["position"] is not None else 999)

        session_name = "latest"
        if isinstance(sessions, list) and sessions:
            s0 = sessions[0]
            session_name = s0.get("session_name") or "latest"

        return jsonify({
            "ok": True,
            "year": 2026,
            "session": session_name,
            "rows": rows
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "rows": []})

if __name__ == "__main__":
    app.run()
