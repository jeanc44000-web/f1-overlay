import os
import time
from datetime import datetime, timezone, timedelta
import requests
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

OPENF1_USER = os.getenv("OPENF1_USER")
OPENF1_PASS = os.getenv("OPENF1_PASS")

TOKEN_URL = "https://api.openf1.org/token"
API_BASE = "https://api.openf1.org/v1"

_token = None
_token_exp = 0
_cache = {"ts": 0, "data": None}

HTML = """
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>F1 Live Timing</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Anton&display=swap" rel="stylesheet">
  <style>
    :root{
      --bg:#000000;
      --panel:#0b0b0b;
      --panel2:#121212;
      --line:rgba(255,255,255,.08);
      --text:#ffffff;
      --muted:rgba(255,255,255,.65);
      --audi:#ba0000;
      --cadillac:#ffffff;
    }
    *{box-sizing:border-box}
    html,body{margin:0;padding:0;background:#000;color:var(--text);font-family:'Anton',sans-serif;overflow:hidden}
    body{padding:14px}
    .wrap{width:560px}
    .top{
      display:flex;justify-content:space-between;align-items:center;
      padding:16px 18px;margin-bottom:12px;background:linear-gradient(135deg, #111, #050505);
      border:1px solid var(--line);border-radius:18px;
      box-shadow:0 16px 36px rgba(0,0,0,.45);
    }
    .eyebrow{
      font-size:12px;letter-spacing:.22em;text-transform:uppercase;color:var(--muted);
      margin-bottom:6px;font-family:Arial,sans-serif;
    }
    .title{
      font-size:30px;line-height:1;color:var(--text);letter-spacing:.03em;
    }
    .status{
      width:12px;height:12px;border-radius:999px;background:#2ecc71;
      box-shadow:0 0 18px rgba(46,204,113,.7);
      flex-shrink:0;
    }
    .status.off{background:#f39c12;box-shadow:0 0 18px rgba(243,156,18,.7)}
    .table{
      background:var(--panel);border:1px solid var(--line);border-radius:18px;overflow:hidden;
      box-shadow:0 16px 36px rgba(0,0,0,.45);
    }
    table{width:100%;border-collapse:collapse}
    thead th{
      font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);
      padding:12px 16px;text-align:left;border-bottom:1px solid var(--line);font-family:Arial,sans-serif;
    }
    tbody td{
      padding:11px 16px;border-bottom:1px solid rgba(255,255,255,.05);font-size:20px;
    }
    tbody tr:last-child td{border-bottom:none}
    .pos{width:58px;font-weight:700}
    .gap{width:120px;text-align:right;font-variant-numeric:tabular-nums;color:var(--muted)}
    .empty{
      margin-top:12px;padding:14px 16px;border-radius:16px;background:var(--panel2);
      border:1px solid var(--line);color:var(--muted);text-align:center;font-family:Arial,sans-serif
    }
    .hidden{display:none}
    .team-audi{border-left:3px solid var(--audi)}
    .team-cadillac{border-left:3px solid var(--cadillac)}
    .team-cadillac td{color:#fff}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <div class="eyebrow">F1 LIVE TIMING</div>
        <div id="sessionTitle" class="title">Loading...</div>
      </div>
      <div id="statusDot" class="status"></div>
    </div>

    <div class="table">
      <table>
        <thead>
          <tr>
            <th class="pos">Pos</th>
            <th>Nom</th>
            <th class="gap">Gap</th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </div>

    <div id="emptyState" class="empty hidden">aucune séance en cours</div>
  </div>

  <script>
    const teamClass = (team) => {
      const t = (team || '').toLowerCase();
      if (t.includes('audi') || t.includes('sauber')) return 'team-audi';
      if (t.includes('cadillac')) return 'team-cadillac';
      return '';
    };

    async function loadData(){
      const res = await fetch('/api/data');
      const data = await res.json();

      const title = document.getElementById('sessionTitle');
      const rows = document.getElementById('rows');
      const empty = document.getElementById('emptyState');
      const dot = document.getElementById('statusDot');

      if(!data.ok){
        title.textContent = 'aucune séance en cours';
        rows.innerHTML = '';
        empty.classList.remove('hidden');
        dot.classList.add('off');
        return;
      }

      title.textContent = data.session || 'session';
      empty.classList.add('hidden');
      dot.classList.remove('off');

      rows.innerHTML = '';
      (data.rows || []).forEach(r => {
        const tr = document.createElement('tr');
        const cls = teamClass(r.team_name);
        if(cls) tr.className = cls;
        tr.innerHTML = `
          <td class="pos">${r.position ?? ''}</td>
          <td>${r.last_name ?? ''}</td>
          <td class="gap">${r.gap ?? ''}</td>
        `;
        rows.appendChild(tr);
      });
    }

    loadData();
    setInterval(loadData, 5000);
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
        headers={"accept": "application/json","Authorization": f"Bearer {token}"},
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

def pick_current_session():
    sessions = api_get("/sessions", {"year": 2026})
    if not isinstance(sessions, list) or not sessions:
        return None
    now = datetime.now(timezone.utc)
    allowed = {"FP1","FP2","FP3","Qualifying","Sprint Qualifying","Sprint","Race"}
    current = []
    for s in sessions:
        if (s.get("session_name") or "").strip() not in allowed:
            continue
        start = parse_dt(s.get("date_start"))
        end = parse_dt(s.get("date_end"))
        if not start:
            continue
        live_start = start - timedelta(minutes=30)
        live_end = (end + timedelta(minutes=30)) if end else (start + timedelta(hours=6))
        if live_start <= now <= live_end:
            current.append(s)
    if not current:
        return None
    current = sorted(current, key=lambda s: (s.get("date_start") or "", s.get("date_end") or "", str(s.get("session_key") or "")))
    return current[-1]

@app.route("/")
def home():
    return render_template_string(HTML)

@app.route("/api/data")
def data():
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < 5:
        return jsonify(_cache["data"])
    try:
        session = pick_current_session()
        if not session:
            payload = {"ok": False, "error": "aucune séance en cours", "session": "", "rows": []}
            _cache["ts"] = now; _cache["data"] = payload
            return jsonify(payload)

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
            if dn is not None:
                latest_pos[int(dn)] = p

        latest_interval = {}
        for i in intervals if isinstance(intervals, list) else []:
            dn = i.get("driver_number")
            if dn is not None:
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
            rows.append({
                "position": p.get("position"),
                "last_name": d.get("last_name") or d.get("broadcast_name") or "",
                "team_name": d.get("team_name") or "",
                "gap": fmt_gap(gap)
            })

        rows = sorted(rows, key=lambda x: x["position"] if x["position"] is not None else 999)
        payload = {"ok": True, "session": session.get("session_name") or "session", "rows": rows}
        _cache["ts"] = now; _cache["data"] = payload
        return jsonify(payload)
    except Exception as e:
        payload = {"ok": False, "error": str(e), "session": "", "rows": []}
        _cache["ts"] = now; _cache["data"] = payload
        return jsonify(payload)

if __name__ == "__main__":
    app.run()
