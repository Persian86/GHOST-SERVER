/*
 * Ghost Rooms card — Home Assistant rooms as big buttons, then everything in a room.
 * Black + amber, matching the Ghost hub tablet (approved on the canvas 24 Nov 2026).
 *
 *   type: custom:ghost-rooms-card
 *   title: Rooms                       # optional
 *   exclude_areas: [ghost_server, solar] # optional area ids to leave out
 *
 * Rooms come from Home Assistant Areas (entity area, or its device's area), so there's
 * nothing to list by hand. Lights, switches, fans, climate, covers, media, locks,
 * vacuums and scenes in a room get controls; temperature/humidity sensors fill the header.
 */
const GRC_VERSION = "1.0.1";

const CSS = `
:host{display:block}
ha-card{background:#000;color:#f2f2f5;border:0;border-radius:0;padding:14px 16px 20px;font-family:Inter,Roboto,system-ui,sans-serif;min-height:calc(100vh - 56px);box-sizing:border-box}
button{font:inherit;color:inherit;cursor:pointer}
.top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 4px 14px}
.title{font-size:26px;font-weight:600;margin-right:4px}
.sub{flex:1;min-width:160px;color:#a8a8b3;font-size:15px}
.pill{height:46px;padding:0 18px;border-radius:23px;border:1px solid #26262d;background:#0e0e11;font-size:15px;font-weight:500;display:inline-flex;align-items:center;gap:8px}
.pill.amber{background:#ffb347;color:#000;border-color:#ffb347}
.back{padding:0 18px 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.room{min-height:170px;border-radius:24px;border:1px solid #1f1f25;background:#0e0e11;padding:16px;display:flex;flex-direction:column;text-align:left}
.room.on{background:#2b1e0c;border-color:rgba(255,179,71,.55)}
.room .row{display:flex;justify-content:space-between;align-items:flex-start;width:100%}
.badge{width:52px;height:52px;border-radius:17px;background:#17171c;color:#a8a8b3;display:flex;align-items:center;justify-content:center}
.room.on .badge{background:#ffb347;color:#000}
.badge ha-icon{--mdc-icon-size:28px}
.temp{font-size:22px;font-weight:300}
.room .name{margin-top:auto;font-size:20px;font-weight:600}
.room .what{font-size:14px;color:#a8a8b3;margin-top:4px}
.room.on .what{color:#ffc77a}
.section{margin:22px 4px 10px;color:#a8a8b3;font-size:14px;letter-spacing:.06em;text-transform:uppercase}
.chips{display:flex;flex-wrap:wrap;gap:10px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.tile{min-height:132px;border-radius:24px;border:1px solid #1f1f25;background:#0e0e11;padding:16px;display:flex;flex-direction:column;justify-content:space-between;text-align:left;gap:10px}
.tile.off-line{opacity:.45}
.tile.on{background:linear-gradient(160deg,#ffc77a,#ffb347);border-color:#ffb347;color:#2a1b00}
.tile .row{display:flex;justify-content:space-between;align-items:center;width:100%}
.tile .label{font-size:17px;font-weight:600;line-height:1.25}
.tile .state{font-size:14px;color:#a8a8b3}
.tile.on .state{color:#2a1b00;opacity:.8}
.tile ha-icon{--mdc-icon-size:28px}
.tile input[type=range]{width:100%;accent-color:#2a1b00;height:28px;margin:0}
.wide{grid-column:span 2}
@media(max-width:520px){.wide{grid-column:span 1}}
.ctl{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.round{width:52px;height:52px;border-radius:26px;border:0;background:#17171c;display:inline-flex;align-items:center;justify-content:center;font-size:24px}
.mode{height:48px;padding:0 16px;border-radius:24px;border:0;background:#17171c;color:#c8c8d0;font-size:15px}
.mode.sel{background:#2b1e0c;color:#ffc77a;box-shadow:inset 0 0 0 2px #ffb347}
.dial{font-size:46px;font-weight:200;line-height:1}
.media input[type=range],.tile.climate input[type=range]{accent-color:#ffb347}
.empty{color:#a8a8b3;font-size:15px;padding:24px 4px}
`;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const DOMAINS = ["light", "switch", "fan", "climate", "cover", "media_player", "lock", "vacuum", "scene", "sensor"];
const ON = new Set(["on", "open", "opening", "playing", "cleaning", "heat", "cool", "heat_cool", "auto", "dry", "fan_only", "unlocked"]);
const MODES = ["off", "cool", "heat", "heat_cool", "auto", "dry", "fan_only"];
const MODE_WORD = { off: "Off", cool: "Cool", heat: "Heat", heat_cool: "Auto", auto: "Auto", dry: "Dry", fan_only: "Fan" };

class GhostRoomsCard extends HTMLElement {
  setConfig(config) {
    this._config = { title: "Rooms", exclude_areas: [], ...config };
    this._room = null;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }
  getCardSize() { return 12; }
  static getStubConfig() { return { title: "Rooms" }; }

  set hass(hass) {
    this._hass = hass;
    // Don't redraw under a finger that's dragging a slider; draw once it lets go.
    if (this._dragging) { this._pending = true; return; }
    const sig = this._signature();
    if (sig === this._sig) return;
    this._sig = sig;
    this._render();
  }

  /* ── data ─────────────────────────────────────────────────────────────── */
  _rooms() {
    const h = this._hass, skip = new Set(this._config.exclude_areas || []);
    const devArea = {};
    Object.values(h.devices || {}).forEach((d) => (devArea[d.id] = d.area_id));
    // Speakers and TVs bring helper switches (shuffle, repeat, do not disturb): those stay with the player.
    const players = new Set(Object.values(h.entities || {}).filter((e) => e.entity_id.startsWith("media_player.") && e.device_id).map((e) => e.device_id));
    const rooms = {};
    Object.values(h.areas || {}).forEach((a) => { if (!skip.has(a.area_id)) rooms[a.area_id] = { id: a.area_id, name: a.name, icon: a.icon || "mdi:home", items: [] }; });
    Object.values(h.entities || {}).forEach((e) => {
      if (e.hidden || e.entity_category || e.disabled_by) return;
      const domain = e.entity_id.split(".")[0];
      if (!DOMAINS.includes(domain)) return;
      if (domain === "switch" && e.device_id && players.has(e.device_id)) return;
      const area = e.area_id || devArea[e.device_id];
      const st = h.states[e.entity_id];
      if (!area || !rooms[area] || !st) return;
      if (domain === "sensor" && !["temperature", "humidity"].includes(st.attributes.device_class)) return;
      rooms[area].items.push(st);
    });
    return Object.values(rooms).filter((r) => r.items.length).sort((a, b) => a.name.localeCompare(b.name));
  }
  _signature() {
    if (!this._hass) return "";
    const parts = [this._room || ""];
    this._rooms().forEach((r) => r.items.forEach((s) => parts.push(s.entity_id, s.state, s.attributes.brightness, s.attributes.temperature,
      s.attributes.current_temperature, s.attributes.media_title, s.attributes.volume_level, s.attributes.hvac_action)));
    return parts.join("|");
  }
  _name(st, room) {
    let n = st.attributes.friendly_name || st.entity_id;
    if (room && n.toLowerCase().startsWith(room.name.toLowerCase() + " ")) n = n.slice(room.name.length + 1);
    return n;
  }
  _of(room, domain) { return room.items.filter((s) => s.entity_id.startsWith(domain + ".")); }
  _climate(room) {
    const t = this._of(room, "sensor").find((s) => s.attributes.device_class === "temperature" && !isNaN(parseFloat(s.state)));
    const c = this._of(room, "climate").find((s) => s.attributes.current_temperature != null);
    const temp = t ? parseFloat(t.state) : c ? c.attributes.current_temperature : null;
    const hum = this._of(room, "sensor").find((s) => s.attributes.device_class === "humidity" && !isNaN(parseFloat(s.state)));
    return { temp: temp == null ? "" : `${Math.round(temp * 10) / 10}°`, humidity: hum ? `${Math.round(parseFloat(hum.state))}% humidity` : "" };
  }
  _summary(room) {
    const lights = this._of(room, "light").filter((s) => s.state === "on").length;
    const bits = [];
    if (lights) bits.push(`${lights} light${lights > 1 ? "s" : ""} on`);
    const playing = this._of(room, "media_player").find((s) => s.state === "playing");
    if (playing) bits.push(`${this._name(playing, room)} playing`);
    const ac = this._of(room, "climate").find((s) => s.state !== "off" && s.state !== "unavailable");
    if (ac) bits.push(`${MODE_WORD[ac.state] || ac.state} ${ac.attributes.temperature ?? ""}°`.trim());
    const sw = this._of(room, "switch").filter((s) => s.state === "on").length + this._of(room, "fan").filter((s) => s.state === "on").length;
    if (sw) bits.push(`${sw} switch${sw > 1 ? "es" : ""} on`);
    const open = this._of(room, "cover").find((s) => s.state === "open");
    if (open) bits.push(`${this._name(open, room)} open`);
    const unlocked = this._of(room, "lock").find((s) => s.state === "unlocked");
    if (unlocked) bits.push("unlocked");
    return { on: bits.length > 0, text: bits.length ? bits.slice(0, 2).join(" · ") : "All off" };
  }

  /* ── actions ──────────────────────────────────────────────────────────── */
  _call(domain, service, data) { return this._hass.callService(domain, service, data); }
  _toggle(st) {
    const d = st.entity_id.split(".")[0], id = st.entity_id;
    if (d === "scene") return this._call("scene", "turn_on", { entity_id: id });
    if (d === "lock") return this._call("lock", st.state === "locked" ? "unlock" : "lock", { entity_id: id });
    if (d === "cover") return this._call("cover", "toggle", { entity_id: id });
    if (d === "vacuum") return this._call("vacuum", st.state === "cleaning" ? "return_to_base" : "start", { entity_id: id });
    if (d === "media_player") return this._call("media_player", "media_play_pause", { entity_id: id });
    return this._call(d, "toggle", { entity_id: id });
  }

  /* ── drawing ──────────────────────────────────────────────────────────── */
  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const rooms = this._rooms();
    const room = this._room && rooms.find((r) => r.id === this._room);
    if (this._room && !room) this._room = null;
    this.shadowRoot.innerHTML = `<style>${CSS}</style><ha-card>${room ? this._roomView(room) : this._gridView(rooms)}</ha-card>`;
    this._wire(rooms, room);
  }

  _gridView(rooms) {
    const lightsOn = rooms.flatMap((r) => this._of(r, "light")).filter((s) => s.state === "on").length;
    const scenes = Object.values(this._hass.states).filter((s) => s.entity_id.startsWith("scene.")).slice(0, 10);
    const roomScenes = new Set(rooms.flatMap((r) => this._of(r, "scene")).map((s) => s.entity_id));
    const houseScenes = scenes.filter((s) => !roomScenes.has(s.entity_id));
    return `
      <div class="top">
        <span class="title">${esc(this._config.title)}</span>
        <span class="sub">${lightsOn ? `${lightsOn} light${lightsOn > 1 ? "s" : ""} on` : "All lights off"} · ${rooms.length} rooms</span>
        ${lightsOn ? `<button class="pill" data-act="all-off"><ha-icon icon="mdi:lightbulb-group-off-outline"></ha-icon>All lights off</button>` : ""}
      </div>
      ${rooms.length ? `<div class="grid">${rooms.map((r) => {
        const s = this._summary(r), c = this._climate(r);
        return `<button class="room ${s.on ? "on" : ""}" data-room="${esc(r.id)}" aria-label="${esc(r.name)}: ${esc(s.text)}. Open room.">
          <span class="row"><span class="badge"><ha-icon icon="${esc(r.icon)}"></ha-icon></span><span class="temp">${esc(c.temp)}</span></span>
          <span class="name">${esc(r.name)}</span><span class="what">${esc(s.text)}</span></button>`;
      }).join("")}</div>` : `<div class="empty">No rooms yet. Give your devices an Area in Home Assistant and they'll show up here.</div>`}
      ${houseScenes.length ? `<div class="section">Scenes</div><div class="chips">${houseScenes.map((s) =>
        `<button class="pill" data-scene="${esc(s.entity_id)}"><ha-icon icon="${esc(s.attributes.icon || "mdi:palette")}"></ha-icon>${esc(this._name(s))}</button>`).join("")}</div>` : ""}`;
  }

  _roomView(room) {
    const c = this._climate(room), lights = this._of(room, "light");
    const anyLight = lights.some((s) => s.state === "on");
    const tiles = [];
    lights.forEach((s) => tiles.push(this._lightTile(s, room)));
    this._of(room, "climate").forEach((s) => tiles.push(this._climateTile(s, room)));
    this._of(room, "media_player").forEach((s) => tiles.push(this._mediaTile(s, room)));
    ["switch", "fan", "cover", "lock", "vacuum"].forEach((d) => this._of(room, d).forEach((s) => tiles.push(this._simpleTile(s, room))));
    const scenes = this._of(room, "scene");
    return `
      <div class="top">
        <button class="pill back" data-act="back" aria-label="Back to all rooms"><ha-icon icon="mdi:chevron-left"></ha-icon>Rooms</button>
        <span class="title">${esc(room.name)}</span>
        <span class="sub">${esc([c.temp, c.humidity].filter(Boolean).join(" · "))}</span>
        ${anyLight ? `<button class="pill" data-act="room-off"><ha-icon icon="mdi:lightbulb-off-outline"></ha-icon>Lights off</button>` : ""}
      </div>
      ${tiles.length ? `<div class="tiles">${tiles.join("")}</div>` : `<div class="empty">Nothing to control in this room yet.</div>`}
      ${scenes.length ? `<div class="section">${esc(room.name)} scenes</div><div class="chips">${scenes.map((s) =>
        `<button class="pill" data-scene="${esc(s.entity_id)}">${esc(this._name(s, room))}</button>`).join("")}</div>` : ""}`;
  }

  _lightTile(s, room) {
    const on = s.state === "on", pct = on && s.attributes.brightness != null ? Math.round(s.attributes.brightness / 2.55) : null;
    const dims = (s.attributes.supported_color_modes || []).some((m) => m !== "onoff");
    const gone = s.state === "unavailable";
    return `<div class="tile ${on ? "on" : ""} ${gone ? "off-line" : ""}">
      <button class="row" style="all:unset;display:flex;justify-content:space-between;align-items:center;width:100%;cursor:pointer" data-toggle="${esc(s.entity_id)}" aria-label="${esc(this._name(s, room))}, ${on ? "on" : "off"}. Tap to switch ${on ? "off" : "on"}.">
        <ha-icon icon="${esc(s.attributes.icon || (on ? "mdi:lightbulb-on" : "mdi:lightbulb-outline"))}"></ha-icon>
        <span class="state">${s.state === "unavailable" ? "Unavailable" : on ? (pct != null ? pct + "%" : "On") : "Off"}</span></button>
      <span class="label">${esc(this._name(s, room))}</span>
      ${dims && on ? `<input type="range" min="1" max="100" value="${pct ?? 0}" data-bright="${esc(s.entity_id)}" aria-label="${esc(this._name(s, room))} brightness">` : ""}
    </div>`;
  }

  _simpleTile(s, room) {
    const d = s.entity_id.split(".")[0], on = ON.has(s.state);
    const icon = s.attributes.icon || { switch: on ? "mdi:toggle-switch" : "mdi:toggle-switch-off-outline", fan: "mdi:fan", cover: on ? "mdi:blinds-open" : "mdi:blinds",
      lock: s.state === "locked" ? "mdi:lock" : "mdi:lock-open-variant", vacuum: "mdi:robot-vacuum" }[d];
    const word = { locked: "Locked", unlocked: "Unlocked", open: "Open", closed: "Closed", cleaning: "Cleaning", docked: "Docked", returning: "Returning",
      on: "On", off: "Off", unavailable: "Unavailable" }[s.state] || s.state;
    const extra = d === "cover" ? `<span class="ctl">
        <button class="mode" data-svc="cover.open_cover|${esc(s.entity_id)}">Open</button>
        <button class="mode" data-svc="cover.stop_cover|${esc(s.entity_id)}">Stop</button>
        <button class="mode" data-svc="cover.close_cover|${esc(s.entity_id)}">Close</button></span>` : "";
    return `<div class="tile ${on && d !== "lock" ? "on" : ""} ${s.state === "unavailable" ? "off-line" : ""}">
      <button style="all:unset;display:flex;flex-direction:column;gap:10px;cursor:pointer;width:100%" data-toggle="${esc(s.entity_id)}" aria-label="${esc(this._name(s, room))}, ${esc(word)}">
        <span class="row"><ha-icon icon="${esc(icon)}"></ha-icon><span class="state">${esc(word)}</span></span>
        <span class="label">${esc(this._name(s, room))}</span></button>${extra}</div>`;
  }

  _climateTile(s, room) {
    const a = s.attributes, target = a.temperature, cur = a.current_temperature;
    const modes = (a.hvac_modes || []).filter((m) => MODES.includes(m));
    return `<div class="tile wide climate">
      <span class="row"><span class="label">${esc(this._name(s, room))}</span><span class="state">${cur != null ? `Room ${cur}°` : ""}${a.hvac_action ? " · " + esc(a.hvac_action) : ""}</span></span>
      <span class="ctl"><span class="dial">${target != null ? esc(target) + "°" : "—"}</span>
        ${target != null ? `<button class="round" data-temp="${esc(s.entity_id)}|-${a.target_temp_step || 0.5}" aria-label="Lower temperature">−</button>
        <button class="round" data-temp="${esc(s.entity_id)}|${a.target_temp_step || 0.5}" aria-label="Raise temperature">+</button>` : ""}</span>
      <span class="ctl">${modes.map((m) => `<button class="mode ${s.state === m ? "sel" : ""}" data-svc="climate.set_hvac_mode|${esc(s.entity_id)}|${m}">${MODE_WORD[m]}</button>`).join("")}</span>
    </div>`;
  }

  _mediaTile(s, room) {
    const a = s.attributes, playing = s.state === "playing";
    const what = a.media_title ? [a.media_title, a.media_artist].filter(Boolean).join(" — ") : { off: "Off", idle: "Idle", paused: "Paused", standby: "Standby", unavailable: "Unavailable" }[s.state] || s.state;
    return `<div class="tile wide media">
      <span class="row"><span class="label">${esc(this._name(s, room))}</span><span class="state">${esc(a.source || "")}</span></span>
      <span class="state" style="color:#c8c8d0">${esc(what)}</span>
      <span class="ctl">
        <button class="round" data-svc="media_player.media_previous_track|${esc(s.entity_id)}" aria-label="Previous"><ha-icon icon="mdi:skip-previous"></ha-icon></button>
        <button class="round" style="background:#ffb347;color:#000" data-svc="media_player.media_play_pause|${esc(s.entity_id)}" aria-label="${playing ? "Pause" : "Play"}"><ha-icon icon="${playing ? "mdi:pause" : "mdi:play"}"></ha-icon></button>
        <button class="round" data-svc="media_player.media_next_track|${esc(s.entity_id)}" aria-label="Next"><ha-icon icon="mdi:skip-next"></ha-icon></button>
        <button class="round" data-svc="media_player.${s.state === "off" ? "turn_on" : "turn_off"}|${esc(s.entity_id)}" aria-label="${s.state === "off" ? "Turn on" : "Turn off"}"><ha-icon icon="mdi:power"></ha-icon></button>
      </span>
      ${a.volume_level != null ? `<input type="range" min="0" max="100" value="${Math.round(a.volume_level * 100)}" data-volume="${esc(s.entity_id)}" aria-label="Volume">` : ""}
    </div>`;
  }

  _wire(rooms, room) {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-room]").forEach((b) => b.addEventListener("click", () => { this._room = b.dataset.room; this._sig = null; this._render(); }));
    root.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
      const act = b.dataset.act;
      if (act === "back") { this._room = null; this._sig = null; this._render(); return; }
      const lights = (act === "room-off" ? this._of(room, "light") : rooms.flatMap((r) => this._of(r, "light"))).filter((s) => s.state === "on").map((s) => s.entity_id);
      if (lights.length) this._call("light", "turn_off", { entity_id: lights });
    }));
    root.querySelectorAll("[data-scene]").forEach((b) => b.addEventListener("click", () => this._call("scene", "turn_on", { entity_id: b.dataset.scene })));
    root.querySelectorAll("[data-toggle]").forEach((b) => b.addEventListener("click", () => { const st = this._hass.states[b.dataset.toggle]; if (st) this._toggle(st); }));
    root.querySelectorAll("[data-svc]").forEach((b) => b.addEventListener("click", () => {
      const [svc, id, mode] = b.dataset.svc.split("|"), [d, s] = svc.split(".");
      this._call(d, s, mode ? { entity_id: id, hvac_mode: mode } : { entity_id: id });
    }));
    root.querySelectorAll("[data-temp]").forEach((b) => b.addEventListener("click", () => {
      const [id, step] = b.dataset.temp.split("|"), st = this._hass.states[id];
      if (st && st.attributes.temperature != null) this._call("climate", "set_temperature", { entity_id: id, temperature: Math.round((st.attributes.temperature + parseFloat(step)) * 10) / 10 });
    }));
    const slider = (attr, fn) => root.querySelectorAll(`[${attr}]`).forEach((r) => {
      const start = () => { this._dragging = true; };
      const end = () => { this._dragging = false; if (this._pending) { this._pending = false; this._sig = null; this.hass = this._hass; } };
      r.addEventListener("pointerdown", start); r.addEventListener("touchstart", start, { passive: true });
      r.addEventListener("change", () => { fn(r.getAttribute(attr), parseInt(r.value, 10)); end(); });
      r.addEventListener("pointerup", end); r.addEventListener("pointercancel", end); r.addEventListener("touchend", end);
    });
    slider("data-bright", (id, v) => this._call("light", "turn_on", { entity_id: id, brightness_pct: v }));
    slider("data-volume", (id, v) => this._call("media_player", "volume_set", { entity_id: id, volume_level: v / 100 }));
  }
}

if (!customElements.get("ghost-rooms-card")) {
  customElements.define("ghost-rooms-card", GhostRoomsCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "ghost-rooms-card", name: "Ghost Rooms", description: "Rooms as big buttons, then every control in the room (black + amber)." });
  console.info(`%c GHOST-ROOMS-CARD %c ${GRC_VERSION} `, "background:#ffb347;color:#000", "background:#17171c;color:#ffb347");
}
