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
        <span class="team-dot" style="background:${d.color}"></span>
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
    const res = await fetch("/api/current", { cache: "no-store" });
    const data = await res.json();
    meetingTitle.textContent = data.meeting || "Grand Prix";
    sessionTitle.textContent = data.session || "Session";
    renderBoard(data.rows || [], `${data.meeting || "Grand Prix"} • ${data.session || "Session"}`);
  } catch (err) {
    meetingTitle.textContent = "Erreur";
    sessionTitle.textContent = String(err);
  }
}

loadData();
setInterval(loadData, 5000);
