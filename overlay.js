/*
 * F1 live overlay — front.
 * Paramètres d'URL :
 *   ?mode=interval   écart avec la voiture devant (défaut : écart au leader)
 *   ?demo=1          fausses données animées (pour tester sans session live)
 */
const params = new URLSearchParams(location.search);
const MODE = params.get("mode") === "interval" ? "interval" : "gap";
const DEMO = params.get("demo") ? "demo=1" : "";

const ROW_H = 24;

const board = document.getElementById("board");
const overlay = document.getElementById("overlay");
const connectionDot = document.getElementById("connection-dot");
const sessionName = document.getElementById("session-name");
const sessionInfo = document.getElementById("session-info");
const rowsEl = document.getElementById("rows");
const pitLayer = document.getElementById("pit-layer");

const entries = new Map(); // tla -> { row, pit, index, els..., cache }
let lastMessageAt = 0;

/* ---------------- Rendu ---------------- */

function setText(el, cache, key, value) {
  if (cache[key] !== value) {
    el.textContent = value;
    cache[key] = value;
  }
}

function createEntry() {
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `
    <span class="position"></span>
    <span class="team-bar"></span>
    <span class="tla"></span>
    <span class="tyre"></span>
    <span class="gap"></span>
  `;

  const pit = document.createElement("div");
  pit.className = "pit";
  pit.innerHTML = "<span>P</span>";

  return {
    row,
    pit,
    index: -1,
    cache: {},
    position: row.querySelector(".position"),
    bar: row.querySelector(".team-bar"),
    tla: row.querySelector(".tla"),
    tyre: row.querySelector(".tyre"),
    gap: row.querySelector(".gap"),
  };
}

function place(entry, index) {
  const transform = `translateY(${index * ROW_H}px)`;
  entry.row.style.transform = transform;
  entry.pit.style.transform = transform;
}

function flash(entry, gained) {
  entry.row.animate(
    [
      { backgroundColor: gained ? "rgba(70, 200, 110, 0.28)" : "rgba(255, 70, 70, 0.28)" },
      { backgroundColor: "#0b0b0e" },
    ],
    { duration: 1200, easing: "ease-out" }
  );
}

function gapText(driver, index, session) {
  if (driver.out) return { text: "OUT", cls: "out" };

  if (index === 0) {
    if (session.is_race) return { text: "LEADER", cls: "leader" };
    return { text: driver.best_lap || "", cls: "leader" };
  }

  const value = MODE === "interval" ? driver.interval : driver.gap;
  return { text: value || "", cls: "" };
}

function syncRows(drivers, session) {
  const seen = new Set();

  drivers.forEach((driver, index) => {
    seen.add(driver.tla);

    let entry = entries.get(driver.tla);
    const isNew = !entry;

    if (isNew) {
      entry = createEntry();
      entries.set(driver.tla, entry);
      place(entry, index); // avant l'ajout au DOM : pas d'animation depuis le haut
      entry.index = index;
      rowsEl.appendChild(entry.row);
      pitLayer.appendChild(entry.pit);
    } else if (entry.index !== index) {
      flash(entry, index < entry.index);
      place(entry, index);
      entry.index = index;
    }

    const c = entry.cache;
    setText(entry.position, c, "pos", String(index + 1));
    setText(entry.tla, c, "tla", driver.tla);

    if (c.color !== driver.team_color) {
      entry.bar.style.background = driver.team_color;
      c.color = driver.team_color;
    }

    const tyre = (driver.tyre || "").toUpperCase();
    if (c.tyre !== tyre) {
      entry.tyre.textContent = tyre;
      entry.tyre.className = `tyre ${tyre.toLowerCase()}`;
      c.tyre = tyre;
    }

    const gap = gapText(driver, index, session);
    setText(entry.gap, c, "gap", gap.text);
    if (c.gapCls !== gap.cls) {
      entry.gap.className = `gap ${gap.cls}`;
      c.gapCls = gap.cls;
    }

    entry.row.classList.toggle("eliminated", Boolean(driver.eliminated));
    entry.pit.classList.toggle("show", Boolean(driver.pit));
  });

  for (const [tla, entry] of entries) {
    if (!seen.has(tla)) {
      entry.row.remove();
      entry.pit.remove();
      entries.delete(tla);
    }
  }

  const height = `${drivers.length * ROW_H}px`;
  rowsEl.style.height = height;

  // aligne la couche des "P" sur le haut de la liste
  const offset =
    rowsEl.getBoundingClientRect().top - board.getBoundingClientRect().top;
  pitLayer.style.top = `${offset}px`;
  pitLayer.style.height = height;
}

function infoText(session, hasData) {
  if (!hasData) return "";

  const status = String(session.status || "").toLowerCase();
  if (status === "finished" || status === "finalised" || status === "ends") {
    return "FINISHED";
  }

  if (session.is_race) {
    if (session.current_lap && session.total_laps) {
      return `LAP ${session.current_lap}/${session.total_laps}`;
    }
    return session.current_lap ? `LAP ${session.current_lap}` : "";
  }

  const part = session.part ? `Q${session.part}  ` : "";
  return session.remaining ? `${part}${session.remaining}` : "";
}

function setDot(state) {
  connectionDot.classList.remove("online", "waiting", "offline");
  connectionDot.classList.add(state);
}

function render(data) {
  lastMessageAt = Date.now();

  const track = data.track || {};
  const session = data.session || {};
  const hasData = Boolean(data.has_data);

  overlay.classList.toggle("yellow", track.status === "YELLOW");
  overlay.classList.toggle("red", track.status === "RED");
  overlay.classList.toggle("placeholder", !hasData);

  setDot(data.connected ? (hasData ? "online" : "waiting") : "offline");

  sessionName.textContent = track.label || session.name || "NO SESSION";
  sessionName.classList.toggle("yellow", track.status === "YELLOW");
  sessionName.classList.toggle("red", track.status === "RED");

  sessionInfo.textContent = infoText(session, hasData);

  syncRows(data.drivers || [], session);
}

/* ---------------- Connexion : SSE, avec repli en polling ---------------- */

function query() {
  return DEMO ? `?${DEMO}` : "";
}

function startPolling() {
  console.warn("[F1 OVERLAY] SSE indisponible, passage en polling 1 s");
  const tick = () =>
    fetch(`/api/live${query()}`, { cache: "no-store" })
      .then((r) => r.json())
      .then(render)
      .catch((error) => {
        console.error("[F1 OVERLAY] erreur live :", error);
        setDot("offline");
      });
  tick();
  setInterval(tick, 1000);
}

function startStream() {
  if (!window.EventSource) return startPolling();

  let failures = 0;
  const source = new EventSource(`/api/stream${query()}`);

  source.onmessage = (event) => {
    failures = 0;
    try {
      render(JSON.parse(event.data));
    } catch (error) {
      console.error("[F1 OVERLAY] message illisible :", error);
    }
  };

  source.onerror = () => {
    failures += 1;
    setDot("offline");
    if (failures >= 5) {
      source.close();
      startPolling();
    }
  };
}

// Si plus rien n'arrive pendant 6 s : pastille rouge.
setInterval(() => {
  if (lastMessageAt && Date.now() - lastMessageAt > 6000) setDot("offline");
}, 2000);

startStream();
