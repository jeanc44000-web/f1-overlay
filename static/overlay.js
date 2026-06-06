const rowsEl = document.getElementById("rows");
const meetingEl = document.getElementById("meeting");
const sessionEl = document.getElementById("session");

function render(rows) {
  rowsEl.innerHTML = "";
  rows.forEach((r, i) => {
    const row = document.createElement("div");
    row.className = "row" + (i === 0 ? " leader" : "");
    row.innerHTML = `
      <div class="pos">${r.pos}</div>
      <div class="driver">
        <span class="dot" style="background:${r.color}"></span>
        <span>${r.driver}</span>
      </div>
      <div class="time">${r.time_text}</div>
      <div class="gap">${r.gap_text}</div>
    `;
    rowsEl.appendChild(row);
  });
}

async function load() {
  try {
    const res = await fetch("/api/current", { cache: "no-store" });
    const data = await res.json();
    meetingEl.textContent = data.meeting || "OpenF1";
    sessionEl.textContent = data.session || "";
    render(data.rows || []);
  } catch (e) {
    meetingEl.textContent = "Erreur";
    sessionEl.textContent = String(e);
  }
}

load();
setInterval(load, 5000);
