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

  eventSource = new EventSource("/api/stream");

  eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    render(data);
  };

  eventSource.onerror = () => {
    setTimeout(connect, 2000);
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

function createRow(driver) {
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

  sessionName.textContent =
    track.label || data.session.name || "NO SESSION";

  sessionName.classList.toggle("yellow", track.status === "YELLOW");
  sessionName.classList.toggle("red", track.status === "RED");

  sessionInfo.textContent = getSessionInfo(data);

  const drivers = data.drivers;
  const existingRows = new Map();

  for (const child of Array.from(rowsContainer.children)) {
    existingRows.set(child.dataset.tla, child);
  }

  rowsContainer.innerHTML = "";

  for (const driver of drivers) {
    const row = existingRows.get(driver.tla) || createRow(driver);
    row.dataset.tla = driver.tla;

    updateRow(row, driver, previousDrivers.get(driver.tla));
    rowsContainer.appendChild(row);

    previousDrivers.set(driver.tla, driver);
  }
}

connect();
setInterval(() => {
  sessionInfo.textContent = getSessionInfo({
    session: stateSession(),
    connected: true,
  });
}, 1000);

function stateSession() {
  return {
    name: sessionName.textContent,
    time_remaining: "",
    current_lap: "",
    total_laps: "",
  };
}
