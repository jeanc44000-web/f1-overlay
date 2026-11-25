const API_URL = "https://TON-APP.onrender.com/api/stream";
const LAST_RESULT_URL = "https://TON-APP.onrender.com/api/last-result";

const overlay = document.getElementById("overlay");
const sessionName = document.getElementById("session-name");
const sessionInfo = document.getElementById("session-info");
const rowsContainer = document.getElementById("rows");

let previousDrivers = new Map();
let eventSource = null;

function connect() {
  if (eventSource) {
    eventSource.close();
  }

  loadLastResult();
  startLiveStream();
}

function loadLastResult() {
  fetch(LAST_RESULT_URL)
    .then((response) => response.json())
    .then((data) => {
      if (data.drivers && data.drivers.length > 0) {
        render(data);
      }
    })
    .catch(() => {});
}

function startLiveStream() {
  eventSource = new EventSource(API_URL);

  eventSource.onmessage = (event) => {
    render(JSON.parse(event.data));
  };

  eventSource.onerror = () => {
    eventSource.close();
    setTimeout(startLiveStream, 2000);
  };
}

function formatClock() {
  return new Date().toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function getSessionInfo(data) {
  const session = data.session;

  if (!data.connected || session.name === "NO SESSION") {
    return `NO SESSION  •  ${formatClock()}`;
  }

  if (session.name === "RACE") {
    if (session.current_lap && session.total_laps) {
      return `LAP ${session.current_lap}/${session.total_laps}`;
    }

    if (session.current_lap) {
      return `LAP ${session.current_lap}`;
    }

    return formatClock();
  }

  if (session.time_remaining) {
    return `${session.time_remaining}  •  ${formatClock()}`;
  }

  return formatClock();
}

function createRow() {
  const row = document.createElement("div");
  row.className = "row";

  row.innerHTML = `
    <span class="position"></span>
    <span class="tla"></span>
    <span class="gap"></span>
    <span class="tyre"></span>
  `;

  return row;
}

function updateRow(row, driver, previous) {
  row.querySelector(".position").textContent = driver.position;
  row.querySelector(".tla").textContent = driver.tla;
  row.querySelector(".tla").style.color = driver.team_color;

  const gap = driver.interval || driver.gap || "";

  if (driver.position === 1) {
    row.querySelector(".gap").textContent = "LEADER";
  } else if (gap) {
    row.querySelector(".gap").textContent = `+${gap}`;
  } else {
    row.querySelector(".gap").textContent = "";
  }

  const tyre = row.querySelector(".tyre");
  const compound = (driver.tyre || "").toUpperCase();

  tyre.textContent = compound;
  tyre.className = `tyre ${compound.toLowerCase()}`;

  row.classList.toggle("eliminated", Boolean(driver.eliminated));

  if (previous) {
    const oldPosition = Number(previous.position);
    const newPosition = Number(driver.position);

    if (newPosition < oldPosition) {
      row.classList.add("gained");
      setTimeout(() => row.classList.remove("gained"), 900);
    } else if (newPosition > oldPosition) {
      row.classList.add("lost");
      setTimeout(() => row.classList.remove("lost"), 900);
    }
  }
}

function render(data) {
  const track = data.track;

  overlay.classList.toggle("yellow", track.status === "YELLOW");
  overlay.classList.toggle("red", track.status === "RED");

  sessionName.textContent = (
    track.label || data.session.name || "NO SESSION"
  );

  sessionName.classList.toggle("yellow", track.status === "YELLOW");
  sessionName.classList.toggle("red", track.status === "RED");

  sessionInfo.textContent = getSessionInfo(data);

  rowsContainer.innerHTML = "";

  for (const driver of data.drivers) {
    const row = createRow();
    const previous = previousDrivers.get(driver.tla);

    updateRow(row, driver, previous);

    rowsContainer.appendChild(row);
    previousDrivers.set(driver.tla, driver);
  }
}

connect();

setInterval(() => {
  if (!sessionInfo.textContent.includes("•")) {
    sessionInfo.textContent = `NO SESSION  •  ${formatClock()}`;
  }
}, 1000);
