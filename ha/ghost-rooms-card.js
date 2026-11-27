/*
 * Ghost Rooms card — Home Assistant rooms as big buttons, then everything in a room.
 * Black + amber, matching the Ghost hub tablet (approved on the canvas 24 Nov 2026).
 *
 *   type: custom:ghost-rooms-card
 *   title: Rooms                       # optional
 *   exclude_areas: [ghost_server, solar] # optional area ids to leave out
 *   names:  {living_room: Lounge, light.x: Lamp}  # set from the card's edit mode (pencil)
 *   icons:  {living_room: mdi:sofa}
 *   hidden: [office, switch.y]
 *   order:  [kitchen, living_room]
 *   extra:  {garden: [switch.pump, script.water]}  # devices added to a room in edit mode
 *
 * Rooms come from Home Assistant Areas (entity area, or its device's area), so there's
 * nothing to list by hand. Lights, switches, fans, climate, covers, media, locks,
 * vacuums and scenes in a room get controls; temperature/humidity sensors fill the header.
 */
const GRC_VERSION = "1.2.0";

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
.edit{border-style:dashed;border-color:#4a3415;background:#0e0e11;gap:10px;justify-content:flex-start;min-height:0}
.edit.hid{opacity:.5}
.edit input[type=text]{width:100%;box-sizing:border-box;height:44px;border-radius:12px;border:1px solid #33333b;background:#17171c;color:#f2f2f5;padding:0 12px;font:inherit;font-size:16px}
.edit input[type=text]:focus{outline:2px solid #ffb347;border-color:#ffb347}
.edit .small{height:38px;font-size:14px;color:#c8c8d0}
.edit .ctl .mode{height:40px;padding:0 12px;font-size:14px}
.hint{color:#a8a8b3;font-size:14px;margin:-4px 4px 14px}
.results{display:flex;flex-direction:column;gap:6px;max-height:260px;overflow:auto}
.results button{all:unset;cursor:pointer;padding:10px 12px;border-radius:12px;background:#17171c;font-size:15px}
.results button small{display:block;color:#8e8e99;font-size:12px}
.results button:focus-visible,.results button:hover{outline:2px solid #ffb347}
.add{grid-column:span 2}
.grid.editing{grid-template-columns:repeat(auto-fill,minmax(250px,1fr))}
`;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const DOMAINS = ["light", "switch", "fan", "climate", "cover", "media_player", "lock", "vacuum", "scene", "sensor"];
// Anything that can be added to a room by hand in edit mode.
const ADDABLE = ["light", "switch", "fan", "climate", "cover", "media_player", "lock", "vacuum", "scene", "script", "button", "input_boolean", "automation", "sensor"];
const ON = new Set(["on", "open", "opening", "playing", "cleaning", "heat", "cool", "heat_cool", "auto", "dry", "fan_only", "unlocked"]);
const MODES = ["off", "cool", "heat", "heat_cool", "auto", "dry", "fan_only"];
const MODE_WORD = { off: "Off", cool: "Cool", heat: "Heat", heat_cool: "Auto", auto: "Auto", dry: "Dry", fan_only: "Fan" };

class GhostRoomsCard extends HTMLElement {
  setConfig(config) {
    this._config = { title: "Rooms", exclude_areas: [], names: {}, icons: {}, hidden: [], order: [], extra: {}, ...config };
    // Keep the open room when the dashboard reloads after a save.
    this._room = this._room || null;
    this._sig = null;
    if (this._hass) Promise.resolve().then(() => this._render());
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }
  getCardSize() { return 12; }
  static getStubConfig() { return { title: "Rooms" }; }

  set hass(hass) {
    this._hass = hass;
    // Don't redraw under a finger that's dragging a slider, or over names being typed.
    if (this._dragging || (this._edit && this.shadowRoot && this.shadowRoot.childElementCount)) { this._pending = true; return; }
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
    const cfg = this._config, names = cfg.names || {}, icons = cfg.icons || {}, hidden = new Set(cfg.hidden || []);
    Object.values(h.areas || {}).forEach((a) => { if (!skip.has(a.area_id)) rooms[a.area_id] = {
      id: a.area_id, haName: a.name, name: names[a.area_id] || a.name, icon: icons[a.area_id] || a.icon || "mdi:home",
      hidden: hidden.has(a.area_id), items: [] }; });
    Object.values(h.entities || {}).forEach((e) => {
      if (e.hidden || e.entity_category || e.disabled_by) return;
      const domain = e.entity_id.split(".")[0];
      if (!DOMAINS.includes(domain)) return;
      if (domain === "switch" && e.device_id && players.has(e.device_id)) return;
      const area = e.area_id || devArea[e.device_id];
      const st = h.states[e.entity_id];
      if (!area || !rooms[area] || !st) return;
      if (domain === "sensor" && !["temperature", "humidity"].includes(st.attributes.device_class)) return;
      if (hidden.has(e.entity_id) && !this._edit) return;
      rooms[area].items.push(st);
    });
    // Devices added to a room by hand (any room can borrow any device).
    Object.entries(cfg.extra || {}).forEach(([area, ids]) => {
      const r = rooms[area]; if (!r) return;
      (ids || []).forEach((id) => {
        const st = h.states[id];
        if (!st || r.items.some((x) => x.entity_id === id) || (hidden.has(id) && !this._edit)) return;
        r.items.push(st);
      });
    });
    const order = cfg.order || [], rank = (r) => { const i = order.indexOf(r.id); return i < 0 ? 1e6 : i; };
    return Object.values(rooms).filter((r) => r.items.length && (this._edit || !r.hidden))
      .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  }
  _signature() {
    if (!this._hass) return "";
    const parts = [this._room || ""];
    this._rooms().forEach((r) => r.items.forEach((s) => parts.push(s.entity_id, s.state, s.attributes.brightness, s.attributes.temperature,
      s.attributes.current_temperature, s.attributes.media_title, s.attributes.volume_level, s.attributes.hvac_action)));
    return parts.join("|");
  }
  _haName(st, room) {
    let n = st.attributes.friendly_name || st.entity_id;
    if (room && n.toLowerCase().startsWith(room.haName.toLowerCase() + " ")) n = n.slice(room.haName.length + 1);
    return n;
  }
  _name(st, room) { return (this._config.names || {})[st.entity_id] || this._haName(st, room); }
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
    if (d === "script") return this._call("script", "turn_on", { entity_id: id });
    if (d === "button") return this._call("button", "press", { entity_id: id });
    return this._call(d, "toggle", { entity_id: id });
  }

  /* ── drawing ──────────────────────────────────────────────────────────── */
  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const rooms = this._rooms();
    const room = this._room && rooms.find((r) => r.id === this._room);
    if (this._room && !room) this._room = null;
    const body = this._edit ? (room ? this._editRoom(room) : this._editGrid(rooms)) : room ? this._roomView(room) : this._gridView(rooms);
    this.shadowRoot.innerHTML = `<style>${CSS}</style><ha-card>${body}</ha-card>`;
    if (this._edit) this._wireEdit(rooms, room); else this._wire(rooms, room);
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
        ${this._canEdit() ? `<button class="pill" data-act="edit" aria-label="Edit rooms: rename, hide, reorder"><ha-icon icon="mdi:pencil"></ha-icon>Edit</button>` : ""}
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
    ["switch", "input_boolean", "fan", "cover", "lock", "vacuum", "script", "button", "automation"].forEach((d) => this._of(room, d).forEach((s) => tiles.push(this._simpleTile(s, room))));
    const scenes = this._of(room, "scene");
    return `
      <div class="top">
        <button class="pill back" data-act="back" aria-label="Back to all rooms"><ha-icon icon="mdi:chevron-left"></ha-icon>Rooms</button>
        <span class="title">${esc(room.name)}</span>
        <span class="sub">${esc([c.temp, c.humidity].filter(Boolean).join(" · "))}</span>
        ${anyLight ? `<button class="pill" data-act="room-off"><ha-icon icon="mdi:lightbulb-off-outline"></ha-icon>Lights off</button>` : ""}
        ${this._canEdit() ? `<button class="pill" data-act="edit" aria-label="Edit this room: rename or hide devices"><ha-icon icon="mdi:pencil"></ha-icon>Edit</button>` : ""}
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
      lock: s.state === "locked" ? "mdi:lock" : "mdi:lock-open-variant", vacuum: "mdi:robot-vacuum", script: "mdi:play-circle-outline",
      button: "mdi:gesture-tap-button", input_boolean: on ? "mdi:toggle-switch" : "mdi:toggle-switch-off-outline", automation: "mdi:robot" }[d] || "mdi:gesture-tap";
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

  /* ── edit mode: rename, re-icon, hide and reorder; saved into this dashboard ── */
  _canEdit() { return !!(this._hass && this._hass.user && this._hass.user.is_admin); }
  _startEdit() {
    const c = this._config;
    const extra = {}; Object.entries(c.extra || {}).forEach(([k, v]) => (extra[k] = [...(v || [])]));
    this._draft = { names: { ...(c.names || {}) }, icons: { ...(c.icons || {}) }, hidden: [...(c.hidden || [])], order: [...(c.order || [])], extra };
    this._saved = this._config;
    this._config = { ...c, ...this._draft };
    this._edit = true; this._sig = null; this._render();
  }
  _stopEdit() {
    this._edit = false; this._query = ""; this._config = this._saved || this._config; this._sig = null; this._pending = false; this._render();
  }
  _editTop(title, hint) {
    return `<div class="top">
        ${this._room ? `<button class="pill back" data-edit="back"><ha-icon icon="mdi:chevron-left"></ha-icon>All rooms</button>` : ""}
        <span class="title">${esc(title)}</span><span class="sub"></span>
        <button class="pill" data-edit="cancel">Cancel</button>
        <button class="pill amber" data-edit="save"><ha-icon icon="mdi:check"></ha-icon>Save</button>
      </div><div class="hint">${esc(hint)}</div>`;
  }
  _editGrid(rooms) {
    return this._editTop("Edit rooms", "Rename a room or give it an icon (e.g. mdi:sofa). Arrows reorder; the eye hides a room. Leave a name empty to use Home Assistant's. Open a room to rename or hide its devices.") +
      `<div class="grid editing">${rooms.map((r, i) => `<div class="room edit ${r.hidden ? "hid" : ""}">
        <span class="row"><span class="badge"><ha-icon icon="${esc(r.icon)}"></ha-icon></span>
          <span class="ctl"><button class="mode" data-move="${esc(r.id)}|-1" aria-label="Move ${esc(r.name)} earlier" ${i === 0 ? "disabled" : ""}>◀</button>
          <button class="mode" data-move="${esc(r.id)}|1" aria-label="Move ${esc(r.name)} later" ${i === rooms.length - 1 ? "disabled" : ""}>▶</button>
          <button class="mode" data-hide="${esc(r.id)}" aria-label="${r.hidden ? "Show" : "Hide"} ${esc(r.name)}"><ha-icon icon="${r.hidden ? "mdi:eye-off" : "mdi:eye"}"></ha-icon></button></span></span>
        <input type="text" data-name="${esc(r.id)}" value="${esc((this._draft.names || {})[r.id] || "")}" placeholder="${esc(r.haName)}" aria-label="Name for ${esc(r.haName)}">
        <input type="text" class="small" data-icon="${esc(r.id)}" value="${esc((this._draft.icons || {})[r.id] || "")}" placeholder="${esc(r.icon)}" aria-label="Icon for ${esc(r.haName)}">
        <button class="mode" data-open="${esc(r.id)}">Edit devices ›</button>
      </div>`).join("")}</div>`;
  }
  _editRoom(room) {
    const hidden = new Set(this._draft.hidden);
    const added = new Set((this._draft.extra || {})[room.id] || []);
    const items = room.items.filter((s) => s.entity_id.split(".")[0] !== "sensor" || added.has(s.entity_id));
    return this._editTop(`Edit ${room.name}`, "Rename a device or hide it from this room. Leave a name empty to use Home Assistant's. Add any other device with the search box.") +
      `<div class="tiles">
      <div class="tile edit add">
        <span class="label"><ha-icon icon="mdi:plus-circle-outline"></ha-icon> Add a device</span>
        <input type="text" data-search placeholder="Search lights, switches, scripts…" aria-label="Search for a device to add to ${esc(room.name)}" value="${esc(this._query || "")}">
        <div class="results" data-results></div>
      </div>
      ${items.map((s) => `<div class="tile edit ${hidden.has(s.entity_id) ? "hid" : ""}">
        <span class="row"><span class="state">${esc(s.entity_id.split(".")[0].replace("_", " "))}${added.has(s.entity_id) ? " · added" : ""}</span>
          ${added.has(s.entity_id) ? `<button class="mode" data-remove="${esc(s.entity_id)}" aria-label="Remove ${esc(this._haName(s, room))} from ${esc(room.name)}">Remove</button>` : `<button class="mode" data-hide="${esc(s.entity_id)}" aria-label="${hidden.has(s.entity_id) ? "Show" : "Hide"} ${esc(this._haName(s, room))}"><ha-icon icon="${hidden.has(s.entity_id) ? "mdi:eye-off" : "mdi:eye"}"></ha-icon></button>`}</span>
        <input type="text" data-name="${esc(s.entity_id)}" value="${esc((this._draft.names || {})[s.entity_id] || "")}" placeholder="${esc(this._haName(s, room))}" aria-label="Name for ${esc(this._haName(s, room))}">
      </div>`).join("")}</div>`;
  }
  _readInputs() {
    const d = this._draft;
    this.shadowRoot.querySelectorAll("[data-name]").forEach((i) => { const v = i.value.trim(); if (v) d.names[i.dataset.name] = v; else delete d.names[i.dataset.name]; });
    this.shadowRoot.querySelectorAll("[data-icon]").forEach((i) => { const v = i.value.trim(); if (/^[a-z]+:[a-z0-9-]+$/.test(v)) d.icons[i.dataset.icon] = v; else delete d.icons[i.dataset.icon]; });
    this._config = { ...this._saved, ...d };
  }
  _wireEdit(rooms, room) {
    const root = this.shadowRoot, redraw = () => { this._readInputs(); this._sig = null; this._render(); };
    root.querySelectorAll("[data-hide]").forEach((b) => b.addEventListener("click", () => {
      const id = b.dataset.hide, h = this._draft.hidden, i = h.indexOf(id);
      if (i < 0) h.push(id); else h.splice(i, 1);
      redraw();
    }));
    root.querySelectorAll("[data-move]").forEach((b) => b.addEventListener("click", () => {
      const [id, dir] = b.dataset.move.split("|");
      const ids = rooms.map((r) => r.id), i = ids.indexOf(id), j = i + parseInt(dir, 10);
      if (j < 0 || j >= ids.length) return;
      [ids[i], ids[j]] = [ids[j], ids[i]];
      this._draft.order = ids;
      redraw();
    }));
    if (room) {
      const box = root.querySelector("[data-search]"), out = root.querySelector("[data-results]");
      const show = () => {
        this._query = box.value;
        const q = box.value.trim().toLowerCase(), have = new Set(room.items.map((s) => s.entity_id));
        if (q.length < 2) { out.innerHTML = q ? "" : `<small style="color:#8e8e99">Type at least two letters.</small>`; return; }
        const hits = Object.values(this._hass.states).filter((s) => ADDABLE.includes(s.entity_id.split(".")[0]) && !have.has(s.entity_id) &&
          ((s.attributes.friendly_name || "").toLowerCase().includes(q) || s.entity_id.includes(q))).slice(0, 12);
        out.innerHTML = hits.length ? hits.map((s) => `<button data-add="${esc(s.entity_id)}">${esc(s.attributes.friendly_name || s.entity_id)}<small>${esc(s.entity_id)}</small></button>`).join("")
          : `<small style="color:#8e8e99">Nothing matches “${esc(box.value.trim())}”.</small>`;
        out.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () => {
          const ex = this._draft.extra; (ex[room.id] = ex[room.id] || []).push(b.dataset.add);
          const hi = this._draft.hidden.indexOf(b.dataset.add); if (hi >= 0) this._draft.hidden.splice(hi, 1);
          this._query = ""; redraw();
        }));
      };
      box.addEventListener("input", show); show();
      root.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", () => {
        const list = this._draft.extra[room.id] || []; const i = list.indexOf(b.dataset.remove);
        if (i >= 0) list.splice(i, 1); if (!list.length) delete this._draft.extra[room.id];
        redraw();
      }));
    }
    root.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => { this._readInputs(); this._room = b.dataset.open; this._sig = null; this._render(); }));
    root.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", async () => {
      const act = b.dataset.edit;
      if (act === "back") { this._readInputs(); this._room = null; this._sig = null; this._render(); return; }
      if (act === "cancel") { this._stopEdit(); return; }
      this._readInputs();
      b.disabled = true; b.textContent = "Saving…";
      try { await this._save(); this._saved = { ...this._saved, ...this._draft }; this._stopEdit(); }
      catch (e) { b.disabled = false; b.textContent = "Save"; alert("Couldn't save: " + (e && e.message ? e.message : e)); }
    }));
  }
  /** Write the names, icons, hidden items and order into this card's entry in the dashboard. */
  async _save() {
    const path = (window.location.pathname.split("/")[1]) || null;
    const urlPath = path === "lovelace" ? null : path;
    const lovelace = await this._hass.callWS({ type: "lovelace/config", url_path: urlPath });
    const d = this._draft, clean = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v));
    let found = 0;
    const visit = (node) => {
      if (Array.isArray(node)) return node.forEach(visit);
      if (!node || typeof node !== "object") return;
      if (node.type === "custom:ghost-rooms-card") {
        found++;
        node.names = clean(d.names); node.icons = clean(d.icons); node.hidden = [...new Set(d.hidden)]; node.order = d.order;
        node.extra = Object.fromEntries(Object.entries(d.extra || {}).filter(([, v]) => v && v.length).map(([k, v]) => [k, [...new Set(v)]]));
        ["names", "icons", "extra"].forEach((k) => { if (!Object.keys(node[k]).length) delete node[k]; });
        ["hidden", "order"].forEach((k) => { if (!node[k].length) delete node[k]; });
      }
      Object.values(node).forEach(visit);
    };
    visit(lovelace);
    if (!found) throw new Error("this card isn't in the dashboard's saved layout (is the dashboard in YAML mode?)");
    await this._hass.callWS({ type: "lovelace/config/save", url_path: urlPath, config: lovelace });
  }

  _wire(rooms, room) {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-room]").forEach((b) => b.addEventListener("click", () => { this._room = b.dataset.room; this._sig = null; this._render(); }));
    root.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
      const act = b.dataset.act;
      if (act === "back") { this._room = null; this._sig = null; this._render(); return; }
      if (act === "edit") { this._startEdit(); return; }
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
