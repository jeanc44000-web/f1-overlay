const teamColors = {
  "Mercedes": "#00d2be",
  "Ferrari": "#dc0000",
  "McLaren": "#ff8700",
  "Red Bull Racing": "#3671c6",
  "Racing Bulls": "#6692ff",
  "Alpine": "#0090ff",
  "Haas": "#b6babd",
  "Williams": "#64c4ff",
  "Audi": "#b08d57",
  "Aston Martin": "#006f62",
  "Reserve": "#7a7a7a"
};

const teamOrder = [
  "Ferrari",
  "McLaren",
  "Mercedes",
  "Red Bull Racing",
  "Racing Bulls",
  "Aston Martin",
  "Alpine",
  "Williams",
  "Haas",
  "Audi",
  "Reserve"
];

const timingRows = document.getElementById("timing-rows");
const eventRecent = document.getElementById("event-recent");
const meetingTitle = document.getElementById("meeting-title");
const sessionTitle = document.getElementById("session-title");

function formatTime(sec) {
  if (sec == null || isNaN(sec)) return "—";
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(3).padStart(6, "0");
  return `${m}:${s}`;
}

function formatGap(sec) {
  if (sec == null || isNaN(sec) || sec === 0) return "+0.000";
  return `+${sec.toFixed(3)}`;
}

function renderBoard(rows, label) {
  timingRows.innerHTML = "";
  rows.forEach((d, i) => {
    const row = document.createElement("div");
    row.className = "timing-row" + (i === 0 ? " leader" : "");
    row.innerHTML = `
      <div class="timing-pos">${i + 1}</div>
      <div class="timing-driver">
        <span class="team-dot" style="background:#${d.color}"></span>
        <span>${d.name}</span>
      </div>
      <div class="timing-time">${formatTime(d.time)}</div>
      <div class="timing-gap">${formatGap(d.gap)}</div>
    `;
    timingRows.appendChild(row);
  });
  eventRecent.textContent = label;
}

async function loadData() {
  try {
    const res = await fetch("https://api.openf1.org/v1/meetings?meeting_key=latest", { cache: "no-store" });
    const meetings = await res.json();
    const meeting = Array.isArray(meetings) ? meetings[0] : meetings;

    if (meeting) {
      meetingTitle.textContent = `${meeting.meeting_name || "Grand Prix"} • ${meeting.location || meeting.country_name || ""}`.trim();
    }

    const sessionsRes = await fetch("https://api.openf1.org/v1/sessions?meeting_key=latest", { cache: "no-store" });
    const sessions = await sessionsRes.json();
    const sessionList = Array.isArray(sessions) ? sessions : [];
    const session = sessionList[0];

    if (session) {
      sessionTitle.textContent = session.session_name || session.session_type || "Session";
    }

    const sessionKey = session?.session_key || "latest";

    const lapsRes = await fetch(`https://api.openf1.org/v1/laps?session_key=${sessionKey}`, { cache: "no-store" });
    const laps = await lapsRes.json();

    const driversRes = await fetch(`https://api.openf1.org/v1/drivers?session_key=${sessionKey}`, { cache: "no-store" });
    const drivers = await driversRes.json();

    const nameByNumber = {};
    const teamByNumber = {};
    drivers.forEach(d => {
      if (d.driver_number != null) {
        const num = String(d.driver_number);
        nameByNumber[num] = d.broadcast_name || d.full_name || `${d.first_name || ""} ${d.last_name || ""}`.trim() || `#${num}`;
        teamByNumber[num] = d.team_name || "Reserve";
      }
    });

    const bestByDriver = {};
    laps.forEach(l => {
      const num = l.driver_number != null ? String(l.driver_number) : null;
      const raw = l.lap_duration ?? l.duration ?? l.lap_time;
      const sec = typeof raw === "string" ? parseFloat(raw) : Number(raw);
      if (!num || !isFinite(sec)) return;
      if (!bestByDriver[num] || sec < bestByDriver[num]) bestByDriver[num] = sec;
    });

    let data = Object.entries(bestByDriver)
      .map(([num, time]) => ({
        name: nameByNumber[num] || `#${num}`,
        team: teamByNumber[num] || "Reserve",
        color: teamColors[teamByNumber[num]] || "#7a7a7a",
        time
      }))
      .sort((a, b) => {
        const t = teamOrder.indexOf(a.team) - teamOrder.indexOf(b.team);
        return t !== 0 ? t : a.time - b.time;
      })
      .slice(0, 22);

    if (!data.length) {
      timingRows.innerHTML = `<div class="timing-row"><div>—</div><div>Aucune donnée</div><div>—</div><div>—</div></div>`;
      return;
    }

    const leader = Math.min(...data.map(d => d.time));
    data = data.map(d => ({ ...d, gap: d.time - leader }));

    renderBoard(data, `${meeting?.meeting_name || "Grand Prix"} • ${session?.session_name || session?.session_type || "Session"}`);
  } catch (err) {
    meetingTitle.textContent = "Erreur OpenF1";
    sessionTitle.textContent = String(err);
  }
}

loadData();
setInterval(loadData, 5000);
