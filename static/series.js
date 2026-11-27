
(() => {
    const overlay = document.querySelector("[data-series]");

    if (!overlay) return;

    const apiUrl = overlay.dataset.api;
    const head = document.getElementById("timing-head");
    const body = document.getElementById("timing-body");
    const status = document.getElementById("session-status");
    const lastUpdate = document.getElementById("last-update");

    function addCell(row, value, tag = "td") {
        const cell = document.createElement(tag);
        cell.textContent = value ?? "";
        row.appendChild(cell);
    }

    function render(data) {
        status.textContent = data.status || "EN ATTENTE";
        status.classList.toggle("live", data.status === "LIVE");

        if (data.updated_at) {
            const date = new Date(data.updated_at);
            lastUpdate.textContent =
                "Mis à jour à " +
                date.toLocaleTimeString("fr-FR");
        }

        if (!data.rows || data.rows.length === 0) {
            body.innerHTML = "";

            const row = document.createElement("tr");
            const cell = document.createElement("td");

            cell.colSpan = Math.max(data.columns?.length || 1, 1);
            cell.className = "loading";
            cell.textContent = data.error
                ? "Erreur : " + data.error
                : "Aucune donnée de chronométrage détectée.";

            row.appendChild(cell);
            body.appendChild(row);
            return;
        }

        head.innerHTML = "";
        body.innerHTML = "";

        const columns = data.columns || [];

        if (columns.length) {
            const headerRow = document.createElement("tr");

            columns.forEach((column) => {
                addCell(headerRow, column, "th");
            });

            head.appendChild(headerRow);
        }

        data.rows.forEach((item) => {
            const row = document.createElement("tr");

            (item.cells || []).forEach((value) => {
                addCell(row, value);
            });

            body.appendChild(row);
        });
    }

    async function refresh() {
        try {
            const response = await fetch(apiUrl, {
                cache: "no-store"
            });

            if (!response.ok) {
                throw new Error("HTTP " + response.status);
            }

            const data = await response.json();
            render(data);

        } catch (error) {
            status.textContent = "ERREUR DE CONNEXION";
            console.error("Live timing :", error);
        }
    }

    refresh();
    setInterval(refresh, 3000);
})();
