/**
 * goatbot-map-card
 *
 * Renders the Goatbot mower's lawn map (boundary/zones/dock) plus its
 * live position as an SVG, using attributes exposed by the
 * lawn_mower.* entity from the goatbot integration (map_graph,
 * region_info, base_info, x, y, heading, cut_progress).
 *
 * Config:
 *   type: custom:goatbot-map-card
 *   entity: lawn_mower.venku_sekacka
 *
 * Coordinate note: the cloud API's local coordinates put +Y "up" (a
 * typical robotics convention), while SVG's Y grows downward - every
 * point below is mirrored on Y before drawing. If the mower's marker
 * ever tracks the mirror image of its real path, flip the sign back.
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
    return 6;
  }

  _ensureShell() {
    if (this._svg) return;
    this.innerHTML = `
      <ha-card>
        <div class="content">
          <svg id="map" width="100%" height="100%" preserveAspectRatio="xMidYMid meet"></svg>
          <div id="empty" class="empty" hidden>No map yet - create one in the Goatbot app.</div>
        </div>
      </ha-card>
      <style>
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
        .dock { fill: var(--state-icon-color, #03a9f4); }
        .mower { fill: var(--primary-color, #ff9800); stroke: var(--card-background-color); stroke-width: 0.03; }
        .progress { font-size: 0.22px; fill: var(--primary-text-color); }
      </style>
    `;
    this._svg = this.querySelector("#map");
    this._empty = this.querySelector("#empty");
  }

  _renderMissing() {
    this._ensureShell();
    this._svg.hidden = true;
    this._empty.hidden = false;
    this._empty.textContent = `Entity ${this._config.entity} not found`;
  }

  _render(entity) {
    this._ensureShell();
    const a = entity.attributes;
    const mapGraph = a.map_graph;
    if (!mapGraph || mapGraph.length < 2) {
      this._svg.hidden = true;
      this._empty.hidden = false;
      this._empty.textContent = "No active map yet - create one in the Goatbot app.";
      return;
    }
    this._svg.hidden = false;
    this._empty.hidden = true;

    const [minX, minY, w, h] = mapGraph[0];
    const fx = (x) => x;
    const fy = (y) => -y; // mirror: cloud coords are Y-up, SVG is Y-down

    const pad = Math.max(w, h) * 0.1 || 0.5;
    const viewMinY = -(minY + h) - pad;
    this._svg.setAttribute(
      "viewBox",
      `${minX - pad} ${viewMinY} ${w + 2 * pad} ${h + 2 * pad}`
    );

    const boundaryPts = mapGraph
      .slice(1)
      .map(([x, y]) => `${fx(x)},${fy(y)}`)
      .join(" ");

    const parts = [`<polygon class="lawn" points="${boundaryPts}"></polygon>`];

    for (const region of a.region_info || []) {
      const pts = (region.regionTrace || []).map(([x, y]) => `${fx(x)},${fy(y)}`).join(" ");
      if (region.regionType === 2) {
        parts.push(`<polyline class="path" points="${pts}"></polyline>`);
      } else {
        parts.push(`<polygon class="lawn" points="${pts}" opacity="0.5"></polygon>`);
      }
    }

    const iconScale = Math.max(w, h) * 0.045 || 0.15;

    if (a.base_info) {
      const [bx, by, bh] = a.base_info;
      const deg = (-(bh || 0) * 180) / Math.PI;
      parts.push(
        `<g transform="translate(${fx(bx)},${fy(by)}) rotate(${deg})">` +
          `<rect x="${-iconScale}" y="${-iconScale}" width="${iconScale * 2}" height="${iconScale * 2}" class="dock" rx="${iconScale * 0.3}"></rect>` +
          `</g>`
      );
    }

    if (typeof a.x === "number" && typeof a.y === "number") {
      const deg = (-(a.heading || 0) * 180) / Math.PI;
      const s = iconScale * 1.3;
      parts.push(
        `<g transform="translate(${fx(a.x)},${fy(a.y)}) rotate(${deg})">` +
          `<polygon class="mower" points="0,${-s} ${s * 0.8},${s * 0.7} ${-s * 0.8},${s * 0.7}"></polygon>` +
          `</g>`
      );
      if (typeof a.cut_progress === "number") {
        parts.push(
          `<text class="progress" x="${minX - pad + 0.15}" y="${viewMinY + 0.3}">${a.cut_progress}%</text>`
        );
      }
    }

    this._svg.innerHTML = parts.join("");
  }
}

customElements.define("goatbot-map-card", GoatbotMapCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "goatbot-map-card",
  name: "Goatbot Map",
  description: "Live lawn map and mower position for the Goatbot integration.",
});
