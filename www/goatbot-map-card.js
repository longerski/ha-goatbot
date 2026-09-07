/**
 * goatbot-map-card
 *
 * Renders the Goatbot mower's lawn map (boundary/zones/dock), the path
 * already covered, and its live position as an SVG, plus a small stats
 * row - using attributes exposed by the lawn_mower.* entity from the
 * goatbot integration (map_graph, region_info, base_info, x, y, heading,
 * cut_progress, cut_area, total_area, remaining_time, moving_time, trail).
 *
 * Config:
 *   type: custom:goatbot-map-card
 *   entity: lawn_mower.venku_sekacka
 *
 * Coordinate note: the cloud API's local coordinates put +Y "up" (a
 * typical robotics convention), while SVG's Y grows downward - every
 * point below is mirrored on Y before drawing. If the mower's marker
 * ever tracks the mirror image of its real path, flip the sign back.
 *
 * viewBox note: `map_graph[0]` looks like a [minX, minY, width, height]
 * bounding box, but in practice it does NOT tightly bound the actual
 * boundary polygon (observed ~0.8 units off) - so the viewBox here is
 * computed directly from every point actually being drawn instead of
 * trusting that header.
 */
class GoatbotMapCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) {
      throw new Error("goatbot-map-card: 'entity' is required");
    }
    this._config = config;
  }

  set hass(hass) {
    this._hass = hass;
    const entity = hass.states[this._config.entity];
    if (!entity) {
      this._renderMissing();
      return;
    }
    this._render(entity);
  }

  getCardSize() {
    return 7;
  }

  _ensureShell() {
    if (this._svg) return;
    this.innerHTML = `
      <ha-card>
        <div class="content">
          <svg id="map" width="100%" height="100%" preserveAspectRatio="xMidYMid meet"></svg>
          <div id="empty" class="empty" hidden>No map yet - create one in the Goatbot app.</div>
        </div>
        <div id="stats" class="stats"></div>
      </ha-card>
      <style>
        [hidden] { display: none !important; }
        ha-card { overflow: hidden; }
        .content { position: relative; width: 100%; aspect-ratio: 4 / 3; background: var(--card-background-color); }
        svg { display: block; }
        .empty {
          position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
          color: var(--secondary-text-color); font-size: 0.9em; text-align: center; padding: 16px;
          box-sizing: border-box;
        }
        .lawn { fill: color-mix(in srgb, var(--success-color, #4caf50) 22%, var(--card-background-color)); stroke: var(--success-color, #4caf50); stroke-width: 0.05; }
        .path { fill: none; stroke: var(--secondary-text-color); stroke-width: 0.04; stroke-dasharray: 0.12 0.1; opacity: 0.7; }
        .trail { fill: none; stroke: #8d6e63; stroke-width: 0.18; stroke-linecap: round; stroke-linejoin: round; opacity: 0.55; }
        .dock-base { fill: var(--state-icon-color, #03a9f4); }
        .dock-post { fill: var(--state-icon-color, #03a9f4); opacity: 0.85; }
        .dock-bolt { fill: #ffd54f; }
        .mower-body { fill: #ff6f00; stroke: var(--card-background-color); stroke-width: 0.06; }
        .mower-nose { fill: #e65100; }
        .mower-eye { fill: var(--card-background-color); }
        .stats {
          display: flex; flex-wrap: wrap; gap: 4px 16px; padding: 10px 16px;
          font-size: 0.85em; color: var(--primary-text-color); border-top: 1px solid var(--divider-color);
        }
        .stats span.label { color: var(--secondary-text-color); }
        .stats:empty { display: none; }
      </style>
    `;
    this._svg = this.querySelector("#map");
    this._empty = this.querySelector("#empty");
    this._stats = this.querySelector("#stats");
  }

  _renderMissing() {
    this._ensureShell();
    this._svg.hidden = true;
    this._empty.hidden = false;
    this._empty.textContent = `Entity ${this._config.entity} not found`;
    this._stats.innerHTML = "";
  }

  _render(entity) {
    this._ensureShell();
    const a = entity.attributes;
    // While a fresh "Create Map" perimeter lap is in progress, map_graph/
    // region_info/base_info still describe the OLD map (the new one only
    // becomes active once the lap finishes) - showing them alongside the
    // live trail mixes two unrelated coordinate spaces and looks broken.
    // Ignore the stale map entirely and just track the live trail instead.
    const mapping = a.work_mode === "mapping";
    const mapGraph = !mapping ? a.map_graph : null;
    if (!mapping && (!mapGraph || mapGraph.length < 2)) {
      this._svg.hidden = true;
      this._empty.hidden = false;
      this._empty.textContent = "No active map yet - create one in the Goatbot app.";
      this._stats.innerHTML = "";
      return;
    }
    this._svg.hidden = false;
    this._empty.hidden = true;

    const fx = (x) => x;
    const fy = (y) => -y; // mirror: cloud coords are Y-up, SVG is Y-down

    const boundary = mapGraph ? mapGraph.slice(1) : [];
    const trail = Array.isArray(a.trail) ? a.trail : [];

    // Bounding box from every point actually drawn - map_graph[0]'s stated
    // bounding box doesn't reliably match the boundary polygon (see file
    // header), so don't use it for the viewBox.
    const allPoints = [
      ...boundary,
      ...(mapping ? [] : (a.region_info || []).flatMap((r) => r.regionTrace || [])),
      ...trail,
    ];
    if (!mapping && a.base_info) allPoints.push(a.base_info);
    if (typeof a.x === "number" && typeof a.y === "number") allPoints.push([a.x, a.y]);
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const [x, y] of allPoints) {
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
    if (!Number.isFinite(minX)) {
      // No points at all yet (e.g. mapping just started, no trace received).
      minX = -2; maxX = 2; minY = -2; maxY = 2;
    }
    const w = maxX - minX || 1;
    const h = maxY - minY || 1;
    const pad = Math.max(w, h) * 0.12 || 0.5;

    // Flipped-Y bounding box: fy(y) = -y, so native [minY, maxY] becomes
    // flipped [-maxY, -minY].
    const viewMinX = minX - pad;
    const viewMinY = -maxY - pad;
    const viewW = w + 2 * pad;
    const viewH = h + 2 * pad;
    this._svg.setAttribute("viewBox", `${viewMinX} ${viewMinY} ${viewW} ${viewH}`);

    const parts = [];
    if (boundary.length) {
      const boundaryPts = boundary.map(([x, y]) => `${fx(x)},${fy(y)}`).join(" ");
      parts.push(`<polygon class="lawn" points="${boundaryPts}"></polygon>`);
    }

    if (!mapping) {
      for (const region of a.region_info || []) {
        const pts = (region.regionTrace || []).map(([x, y]) => `${fx(x)},${fy(y)}`).join(" ");
        if (region.regionType === 2) {
          parts.push(`<polyline class="path" points="${pts}"></polyline>`);
        } else {
          parts.push(`<polygon class="lawn" points="${pts}" opacity="0.5"></polygon>`);
        }
      }
    }

    if (trail.length > 1) {
      const trailPts = trail.map(([x, y]) => `${fx(x)},${fy(y)}`).join(" ");
      parts.push(`<polyline class="trail" points="${trailPts}"></polyline>`);
    }

    const iconScale = Math.max(w, h) * 0.05 || 0.18;

    if (!mapping && a.base_info) {
      const [bx, by, bh] = a.base_info;
      const deg = (-(bh || 0) * 180) / Math.PI;
      // A small charging-station silhouette: a wide base plate the mower
      // parks on, a raised back post with contacts, and a bolt on it -
      // drawn in a fixed unit square and scaled, so proportions stay
      // right at any size.
      parts.push(
        `<g transform="translate(${fx(bx)},${fy(by)}) rotate(${deg}) scale(${iconScale})">` +
          `<rect class="dock-post" x="-0.35" y="-0.75" width="0.7" height="0.85" rx="0.12"></rect>` +
          `<rect class="dock-base" x="-0.95" y="0.05" width="1.9" height="0.55" rx="0.15"></rect>` +
          `<path class="dock-bolt" d="M -0.06,-0.6 L 0.14,-0.6 L -0.02,-0.28 L 0.16,-0.28 L -0.16,0.15 L -0.02,-0.22 L -0.2,-0.22 Z"></path>` +
          `</g>`
      );
    }

    if (typeof a.x === "number" && typeof a.y === "number") {
      const deg = (-(a.heading || 0) * 180) / Math.PI;
      // A rounded-body mower with a directional nose and a little "eye",
      // instead of a bare triangle - reads as a mower rather than an
      // arbitrary marker, and heading is still obvious from the nose.
      parts.push(
        `<g transform="translate(${fx(a.x)},${fy(a.y)}) rotate(${deg}) scale(${iconScale})">` +
          `<rect class="mower-body" x="-0.75" y="-0.85" width="1.5" height="1.7" rx="0.55"></rect>` +
          `<polygon class="mower-nose" points="0,-1.15 0.32,-0.7 -0.32,-0.7"></polygon>` +
          `<circle class="mower-eye" cx="0" cy="-0.25" r="0.16"></circle>` +
          `</g>`
      );
    }

    this._svg.innerHTML = parts.join("");
    if (mapping) {
      this._stats.innerHTML = this._stat("Stav", "Vytváří se nová mapa…");
    } else {
      this._renderStats(a);
    }
  }

  _renderStats(a) {
    const rows = [];
    const totalArea = typeof a.total_area === "number" ? a.total_area : null;
    const cutArea =
      typeof a.cut_area === "number"
        ? a.cut_area
        : totalArea != null && typeof a.cut_progress === "number"
        ? (totalArea * a.cut_progress) / 100
        : null;

    if (totalArea != null) {
      rows.push(this._stat("Plocha", `${totalArea} m²`));
    }
    if (cutArea != null) {
      rows.push(this._stat("Posečeno", `${cutArea.toFixed(1)} m²${typeof a.cut_progress === "number" ? ` (${a.cut_progress}%)` : ""}`));
      if (totalArea != null) {
        rows.push(this._stat("Zbývá", `${Math.max(totalArea - cutArea, 0).toFixed(1)} m²`));
      }
    }
    if (typeof a.moving_time === "number") {
      rows.push(this._stat("Seče", this._formatMinutes(a.moving_time / 60)));
    }
    const remainHours = typeof a.remaining_time === "string" ? parseFloat(a.remaining_time) : a.remaining_time;
    if (typeof remainHours === "number" && !Number.isNaN(remainHours)) {
      rows.push(this._stat("Zbývá čas", this._formatMinutes(remainHours * 60)));
    }
    this._stats.innerHTML = rows.join("");
  }

  _stat(label, value) {
    return `<div><span class="label">${label}:</span> ${value}</div>`;
  }

  _formatMinutes(minutes) {
    if (minutes < 60) return `${Math.round(minutes)} min`;
    const h = Math.floor(minutes / 60);
    const m = Math.round(minutes % 60);
    return `${h} h ${m} min`;
  }
}

customElements.define("goatbot-map-card", GoatbotMapCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "goatbot-map-card",
  name: "Goatbot Map",
  description: "Live lawn map, coverage trail and mower position for the Goatbot integration.",
});
