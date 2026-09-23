/*
 * Fallback renderer for Public Access.
 *
 * This is the minimal renderer bundled with the open repository so the plugin is
 * usable and auditable on its own. The licensed renderer payload replaces it and
 * draws the full card set; when no payload is cached, this runs instead.
 *
 * It only ever reads the public endpoints, which serve allowlisted data.
 */
(() => {
  "use strict";

  const app = document.getElementById("app");
  const base = app.dataset.base;
  const boot = JSON.parse(document.getElementById("bootstrap").textContent || "{}");
  const meta = boot.meta || {};
  const energy = boot.energy || null;
  const cards = (boot.dashboard && boot.dashboard.cards) || [];

  const COLORS = { solar: "var(--accent)", grid: "var(--grid)", export: "var(--export)" };
  let period = "month";
  let stats = null;
  let states = {};

  const fmt = (value, digits = 1) =>
    value === null || value === undefined || Number.isNaN(value)
      ? "–"
      : new Intl.NumberFormat(meta.language || "en", {
          minimumFractionDigits: digits,
          maximumFractionDigits: digits,
        }).format(value);

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  async function fetchJSON(path) {
    const response = await fetch(`${base}/${path}`, { credentials: "omit" });
    if (!response.ok) throw new Error(`${path}: ${response.status}`);
    return response.json();
  }

  /* --- statistics helpers ------------------------------------------------- */

  // Statistics arrive as cumulative sums; the charts want per-bucket deltas.
  function deltas(ids) {
    if (!stats || !stats.series) return [];
    const merged = new Map();
    (Array.isArray(ids) ? ids : [ids]).filter(Boolean).forEach((id) => {
      const points = stats.series[id] || [];
      for (let i = 1; i < points.length; i += 1) {
        const previous = points[i - 1].sum;
        const current = points[i].sum;
        if (previous === undefined || current === undefined) continue;
        const key = points[i].start;
        merged.set(key, (merged.get(key) || 0) + Math.max(0, current - previous));
      }
    });
    return [...merged.entries()].sort((a, b) => a[0] - b[0]).map(([start, value]) => ({ start, value }));
  }

  const total = (ids) => deltas(ids).reduce((sum, point) => sum + point.value, 0);

  function label(epoch) {
    const date = new Date(typeof epoch === "number" ? epoch * 1000 : epoch);
    const options = period === "day" ? { hour: "2-digit" }
      : period === "year" ? { month: "short" }
      : { day: "2-digit", month: "2-digit" };
    return new Intl.DateTimeFormat(meta.language || "en", {
      ...options,
      timeZone: meta.time_zone || undefined,
    }).format(date);
  }

  /* --- chart -------------------------------------------------------------- */

  function barChart(groups) {
    const buckets = new Map();
    groups.forEach((group) => {
      deltas(group.ids).forEach((point) => {
        if (!buckets.has(point.start)) buckets.set(point.start, {});
        buckets.get(point.start)[group.name] = point.value;
      });
    });
    const keys = [...buckets.keys()].sort((a, b) => a - b);
    if (!keys.length) return el("p", "placeholder", "No statistics for this period yet.");

    const W = 720, H = 240, padL = 44, padB = 26, padT = 8;
    const max = Math.max(
      ...keys.map((key) => Math.max(...groups.map((g) => buckets.get(key)[g.name] || 0))),
      0.001
    );
    const step = (W - padL) / keys.length;
    const barW = Math.max(1.5, (step * 0.72) / groups.length);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("class", "chart");
    svg.setAttribute("role", "img");

    const ns = (tag, attrs) => {
      const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
      return node;
    };

    // horizontal guides
    for (let i = 0; i <= 4; i += 1) {
      const y = padT + ((H - padT - padB) * i) / 4;
      svg.appendChild(ns("line", {
        x1: padL, x2: W, y1: y, y2: y,
        stroke: "var(--border)", "stroke-width": 1,
      }));
      const value = max * (1 - i / 4);
      const text = ns("text", { x: padL - 6, y: y + 4, "text-anchor": "end",
        "font-size": 10, fill: "var(--muted)" });
      text.textContent = fmt(value, value < 10 ? 1 : 0);
      svg.appendChild(text);
    }

    keys.forEach((key, index) => {
      groups.forEach((group, gi) => {
        const value = buckets.get(key)[group.name] || 0;
        const height = ((H - padT - padB) * value) / max;
        svg.appendChild(ns("rect", {
          x: padL + index * step + step * 0.14 + gi * barW,
          y: H - padB - height,
          width: barW,
          height: Math.max(0, height),
          fill: group.color,
          rx: 2,
        }));
      });
      const every = Math.ceil(keys.length / 12);
      if (index % every === 0) {
        const text = ns("text", {
          x: padL + index * step + step / 2, y: H - 8,
          "text-anchor": "middle", "font-size": 10, fill: "var(--muted)",
        });
        text.textContent = label(key);
        svg.appendChild(text);
      }
    });

    const wrap = document.createDocumentFragment();
    wrap.appendChild(svg);
    const legend = el("div", "legend");
    groups.forEach((group) => {
      const item = el("span");
      const swatch = el("span", "swatch");
      swatch.style.background = group.color;
      item.append(swatch, document.createTextNode(`${group.label} · ${fmt(total(group.ids))} kWh`));
      legend.appendChild(item);
    });
    wrap.appendChild(legend);
    return wrap;
  }

  /* --- card renderers ----------------------------------------------------- */

  function tile(entityId) {
    const state = states[entityId];
    const node = el("div", "tile");
    const attrs = (state && state.attributes) || {};
    node.append(
      el("span", "label", attrs.friendly_name || entityId),
      (() => {
        const value = el("span", "value", state ? state.state : "–");
        if (attrs.unit_of_measurement) {
          value.append(el("span", "unit", ` ${attrs.unit_of_measurement}`));
        }
        return value;
      })()
    );
    return node;
  }

  function renderCard(card) {
    const box = el("section", "card");
    if (card.title) box.appendChild(el("h2", null, card.title));

    switch (card.type) {
      case "markdown": {
        const md = el("div", "md");
        md.innerHTML = String(card.content || "")
          .replace(/[<>]/g, (c) => ({ "<": "&lt;", ">": "&gt;" }[c]))
          .replace(/^###\s?(.*)$/gm, "<h3>$1</h3>")
          .replace(/^##\s?(.*)$/gm, "<h2>$1</h2>")
          .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
          .split(/\n{2,}/)
          .map((block) => (block.startsWith("<h") ? block : `<p>${block}</p>`))
          .join("");
        box.appendChild(md);
        break;
      }
      case "entities": {
        const rows = el("div", "rows");
        (card.entities || []).forEach((item) => {
          const id = typeof item === "string" ? item : item.entity;
          const state = states[id];
          const row = el("div", "row");
          const attrs = (state && state.attributes) || {};
          row.append(
            el("span", "k", (typeof item === "object" && item.name) || attrs.friendly_name || id),
            el("span", "v", state ? `${state.state}${attrs.unit_of_measurement ? " " + attrs.unit_of_measurement : ""}` : "–")
          );
          rows.appendChild(row);
        });
        box.appendChild(rows);
        break;
      }
      case "gauge":
      case "tile":
      case "sensor": {
        const tiles = el("div", "tiles");
        tiles.appendChild(tile(card.entity));
        box.appendChild(tiles);
        break;
      }
      case "energy-usage-graph": {
        if (!card.title) box.appendChild(el("h2", null, "Energy usage"));
        box.appendChild(barChart([
          { name: "import", label: "From grid", ids: energy && energy.grid.import, color: COLORS.grid },
          { name: "solar", label: "Solar", ids: energy && energy.solar, color: COLORS.solar },
        ]));
        break;
      }
      case "energy-solar-graph": {
        box.appendChild(barChart([
          { name: "solar", label: "Production", ids: energy && energy.solar, color: COLORS.solar },
          { name: "export", label: "To grid", ids: energy && energy.grid.export, color: COLORS.export },
        ]));
        break;
      }
      case "energy-devices-graph": {
        box.appendChild(barChart(
          ((energy && energy.devices) || []).map((device, index) => ({
            name: device.id,
            label: device.name || device.id,
            ids: device.id,
            color: index % 2 ? COLORS.grid : COLORS.accent || COLORS.solar,
          }))
        ));
        break;
      }
      case "statistics-graph": {
        const ids = (card.entities || []).map((item) => (typeof item === "string" ? item : item.entity));
        box.appendChild(barChart([{ name: "series", label: card.title || "Series", ids, color: COLORS.solar }]));
        break;
      }
      case "energy-sources-table": {
        const rows = el("div", "rows");
        const add = (name, ids, unit = "kWh") => {
          if (!ids || (Array.isArray(ids) && !ids.length)) return;
          const row = el("div", "row");
          row.append(el("span", "k", name), el("span", "v", `${fmt(total(ids))} ${unit}`));
          rows.appendChild(row);
        };
        if (energy) {
          add("Solar production", energy.solar);
          add("Imported from grid", energy.grid.import);
          add("Exported to grid", energy.grid.export);
          add("Cost", energy.grid.cost, meta.currency || "");
          add("Compensation", energy.grid.compensation, meta.currency || "");
          (energy.devices || []).forEach((device) => add(device.name || device.id, device.id));
        }
        box.appendChild(rows);
        break;
      }
      case "energy-distribution":
      case "energy-self-consumption-gauge":
      case "energy-grid-neutrality-gauge": {
        const solar = total(energy && energy.solar);
        const exported = total(energy && energy.grid.export);
        const imported = total(energy && energy.grid.import);
        const selfUsed = Math.max(0, solar - exported);
        const consumption = selfUsed + imported;
        const tiles = el("div", "tiles");
        const stat = (labelText, value, unit) => {
          const node = el("div", "tile");
          node.append(el("span", "label", labelText),
            (() => { const v = el("span", "value", fmt(value, value < 10 ? 1 : 0));
              v.append(el("span", "unit", ` ${unit}`)); return v; })());
          return node;
        };
        if (card.type === "energy-self-consumption-gauge") {
          tiles.appendChild(stat("Self-consumption", solar ? (selfUsed / solar) * 100 : 0, "%"));
        } else if (card.type === "energy-grid-neutrality-gauge") {
          tiles.appendChild(stat("Grid neutrality", consumption ? (exported / Math.max(exported + imported, 1)) * 100 : 0, "%"));
        } else {
          tiles.append(
            stat("Solar production", solar, "kWh"),
            stat("Self-consumed", selfUsed, "kWh"),
            stat("Exported", exported, "kWh"),
            stat("Imported", imported, "kWh"),
            stat("Self-sufficiency", consumption ? (selfUsed / consumption) * 100 : 0, "%")
          );
        }
        box.appendChild(tiles);
        break;
      }
      case "energy-date-selection":
        return null; // handled by the period switcher
      case "public-access-unsupported":
        box.appendChild(el("p", "placeholder",
          `This card type (${card.original_type}) is not available on the public view.`));
        break;
      default:
        box.appendChild(el("p", "placeholder", "Unsupported card."));
    }
    return box;
  }

  /* --- page --------------------------------------------------------------- */

  function periodSwitcher() {
    const wrap = el("div", "periods");
    [["day", "Day"], ["week", "Week"], ["month", "Month"], ["year", "Year"]].forEach(([key, text]) => {
      const button = el("button", null, text);
      button.setAttribute("aria-pressed", String(key === period));
      button.addEventListener("click", async () => {
        period = key;
        stats = await fetchJSON(`stats.json?period=${key}`);
        render();
      });
      wrap.appendChild(button);
    });
    return wrap;
  }

  function render() {
    app.textContent = "";
    const header = el("header");
    header.appendChild(el("h1", null, (boot.dashboard && boot.dashboard.title) || "Dashboard"));
    header.appendChild(el("p", "sub", "Read-only public view"));
    app.appendChild(header);

    if (boot.error === "no_dashboard_config") {
      app.appendChild(el("p", "notice", "This dashboard has no saved configuration yet."));
      return;
    }
    if (energy || cards.some((card) => card.type === "energy-date-selection")) {
      app.appendChild(periodSwitcher());
    }
    cards.forEach((card) => {
      const node = renderCard(card);
      if (node) app.appendChild(node);
    });
    const footer = el("footer", null, `Updated ${new Date(boot.generated_at).toLocaleString(meta.language || "en")}`);
    app.appendChild(footer);
  }

  (async () => {
    render();
    try {
      const [statePayload, statPayload] = await Promise.all([
        fetchJSON("states.json"),
        fetchJSON(`stats.json?period=${period}`),
      ]);
      (statePayload.states || []).forEach((state) => { states[state.entity_id] = state; });
      stats = statPayload;
      render();
    } catch (error) {
      app.appendChild(el("p", "notice", "Could not load the dashboard data."));
    }
  })();
})();
