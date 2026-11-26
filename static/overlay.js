const LIVE_URL = "https://TON-APP.onrender.com/api/live";
const LAST_RESULT_URL = "https://TON-APP.onrender.com/api/last-result";

const overlay = document.getElementById("overlay");
const sessionName = document.getElementById("session-name");
const sessionInfo = document.getElementById("session-info");
const rowsContainer = document.getElementById("rows");

let previousDrivers = new Map();
let liveConnected = false;

function log(message) {
  console.log("[F1 OVERLAY]", message);
}

function start() {
  log("Overlay démarré");

  updateClock();
  setInterval(updateClock, 1000);

  loadLastResult();
  loadLive();
  setInterval(loadLive, 1000);
}

function loadLastResult() {
  log("Chargement de la dernière séance");

  fetch(LAST_RESULT_URL)
    .then((response) => response.json())
    .then((data) => {
      log("Dernière séance reçue");

      if (data.drivers && data.drivers.length > 0) {
        render(data);
      }
    })
    .catch((error) => {
      log(`Erreur dernière séance : ${error}`);
    });
}

function loadLive() {
  fetch(LIVE_URL)
    .then((response) => response.json())
    .then((data) => {
      liveConnected = Boolean(
        data.connected && data.drivers.length > 0
      );

      if (liveConnected) {
        render(data);
      }
    })
    .catch((error) => {
      log(`Erreur live : ${error}`);
    });
}

function formatClock() {
  return new Date().toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function updateClock() {
  if (!liveConnected) {
    sessionInfo.textContent = `NO SESSION  •  ${formatClock()}`;
    return;
  }

  const session = latestSession();

  if (!session) {
    sessionInfo.textContent = `NO SESSION  •  ${formatClock()}`;
    return;
  }

  if (session.name === "RACE") {
    if (session.current_lap && session.total_laps) {
      sessionInfo.textContent = `LAP ${session.current_lap}/${session.total_laps}`;
      return;
    }

    if (session.current_lap) {
      sessionInfo.textContent = `LAP ${session.current_lap}`;
      return;
    }

    sessionInfo.textContent = formatClock();
    return;
  }

  if (session.time_remaining) {
    sessionInfo.textContent = `${session.time_remaining}  •  ${formatClock()}`;
    return;
  }

  sessionInfo.textContent = formatClock();
}

let latestSessionData = null;

function latestSession() {
  return latestSessionData;
}

function createRow() {
  const row = document.createElement("div");
  row.className = "row";

  row.innerHTML = `
    <span class="position"></span>
    <span class="tla-wrapper">
      <span class="tla"></span>
      <span class="pit-badge"></span>
    </span>
    <span class="gap"></span>
    <span class="tyre"></span>
  `;

  return row;
}

function updateRow(row, driver, previous) {
  row.querySelector(".position").textContent = driver.position;
  row.querySelector(".tla").textContent = driver.tla;
  row.querySelector(".tla").style.color = driver.team_color;

  const pitBadge = row.querySelector(".pit-badge");
  pitBadge.textContent = driver.pit ? "P" : "";

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
  latestSessionData = data.session;

  const track = data.track;

  overlay.classList.toggle("yellow", track.status === "YELLOW");
  overlay.classList.toggle("red", track.status === "RED");

  sessionName.textContent = (
    track.label || data.session.name || "NO SESSION"
  );

  sessionName.classList.toggle("yellow", track.status === "YELLOW");
  sessionName.classList.toggle("red", track.status === "RED");

  updateClock();

  rowsContainer.innerHTML = "";

  for (const driver of data.drivers) {
    const row = createRow();
    const previous = previousDrivers.get(driver.tla);

    updateRow(row, driver, previous);

    rowsContainer.appendChild(row);
    previousDrivers.set(driver.tla, driver);
  }
}

start();
