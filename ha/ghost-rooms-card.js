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
 * Layout (design R2, chosen 27 Nov 2026): rooms listed down the left with what's on and a
 * movement dot; the chosen room fills the right — photo header (the Area's picture), scenes,
 * lights you drag across to dim, then speakers, aircon, blinds and a camera snapshot.
 * On a phone the room list becomes a row of chips across the top.
 *
 * Rooms come from Home Assistant Areas (entity area, or its device's area), so there's
 * nothing to list by hand. Lights, switches, fans, climate, covers, media, locks,
 * vacuums and scenes in a room get controls; temperature/humidity sensors fill the header.
 */
const GRC_VERSION = "2.2.2";

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
.tile.edit select{width:100%;height:40px;border-radius:12px;border:1px solid #33333b;background:#0e0e11;color:#f2f2f5;font:inherit;padding:0 10px}
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
.focus{display:grid;grid-template-columns:290px minmax(0,1fr);gap:16px;align-items:start}
.list{position:sticky;top:8px;border-radius:24px;background:#0e0e11;border:1px solid #1f1f25;padding:14px 10px;display:flex;flex-direction:column;gap:4px;max-height:calc(100vh - 90px);overflow:auto}
.list .head{display:flex;align-items:baseline;gap:10px;padding:2px 10px 10px}
.list .head b{font-size:24px;font-weight:600}.list .head span{font-size:13px;color:#a8a8b3}
.ritem{all:unset;cursor:pointer;display:flex;align-items:center;gap:12px;min-height:58px;padding:0 12px;border-radius:16px;box-sizing:border-box}
.ritem:hover{background:#141418}.ritem:focus-visible{outline:2px solid #ffb347}
.ritem.sel{background:#2b1e0c;box-shadow:inset 3px 0 0 #ffb347}
.ritem .ic{width:36px;height:36px;flex:none;border-radius:12px;background:#17171c;color:#a8a8b3;display:flex;align-items:center;justify-content:center}
.ritem.on .ic{background:#2b1e0c;color:#ffb347}.ritem.sel.on .ic{background:#ffb347;color:#000}
.ritem .ic ha-icon{--mdc-icon-size:20px}
.ritem .nm{flex:1;min-width:0}.ritem .nm b{display:block;font-size:16px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ritem .nm small{display:block;font-size:12px;color:#a8a8b3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ritem.on .nm small{color:#ffc77a}
.dot{width:9px;height:9px;border-radius:5px;background:#5fd39a;box-shadow:0 0 0 4px rgba(95,211,154,.22);flex:none}
.list .foot{display:flex;gap:8px;flex-wrap:wrap;padding:10px 6px 2px}
.panel{display:flex;flex-direction:column;gap:14px;min-width:0}
.hero{position:relative;min-height:200px;border-radius:24px;overflow:hidden;border:1px solid #2b1e0c;background-size:cover;background-position:center}
.hero:before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(0,0,0,.82) 0%,rgba(0,0,0,.25) 75%)}
.hero .in{position:relative;padding:22px 24px 18px;display:flex;flex-direction:column;gap:14px;min-height:200px;box-sizing:border-box}
.hero .row1{display:flex;align-items:flex-start;gap:10px}
.hero h2{margin:0;font-size:34px;font-weight:600;flex:1}.hero .meta{font-size:15px;color:#e0d6ca;margin-top:6px;font-weight:400}
.glass{height:46px;padding:0 18px;border-radius:23px;border:0;background:rgba(0,0,0,.55);color:#fff;font-size:15px;font-weight:500;display:inline-flex;align-items:center;gap:8px}
.glass.round{width:46px;padding:0;justify-content:center}
.hero .mark{position:absolute;right:28px;bottom:-24px;--mdc-icon-size:190px;color:rgba(255,179,71,.10);pointer-events:none}
.hero .scenes{margin-top:auto;display:flex;gap:8px;flex-wrap:wrap}
.hero .scenes button{height:40px;padding:0 16px;border-radius:20px;border:0;background:rgba(0,0,0,.55);color:#fff;font-size:14px;font-weight:500}
.sep{height:1px;background:#1f1f25;margin:6px 10px}
.ovh{background:radial-gradient(90% 140% at 80% 0%,#5a3a1f 0%,#2b1e0c 40%,#0b0a09 100%)}
.hero .chips{margin-top:auto;display:flex;gap:8px;flex-wrap:wrap}.hero .chips span{height:36px;padding:0 14px;border-radius:18px;background:rgba(0,0,0,.5);display:inline-flex;align-items:center;font-size:14px;color:#ffe2bd}
.card.readout{flex-direction:row;align-items:center;gap:16px}.readout ha-icon{--mdc-icon-size:34px;color:#ffb347}.readout .big{font-size:30px;font-weight:600;line-height:1.1}.readout small{color:#a8a8b3;font-size:13px}
.card.favempty{flex-direction:row;align-items:center;gap:16px}.favempty>ha-icon{--mdc-icon-size:34px;color:#ffb347}.favempty>div{flex:1}.favempty b{display:block;font-size:16px}.favempty small{color:#a8a8b3}
.lbl{font-size:13px;color:#a8a8b3;letter-spacing:.06em;text-transform:uppercase;padding-left:4px}
.bars{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.bar{position:relative;height:70px;border-radius:18px;background:#17171c;overflow:hidden;touch-action:pan-y;user-select:none;-webkit-user-select:none;cursor:pointer}
.bar:focus-visible{outline:2px solid #ffb347}
.bar .fill{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,#ffb347,#ffc77a)}
.bar.full .fill{width:100%}
.bar .txt{position:absolute;inset:0;display:flex;align-items:center;gap:12px;padding:0 16px;font-weight:600;color:#f2f2f5}
.bar.lit .txt{color:#2a1b00}.bar.lit .txt .pct{color:inherit}
.bar .txt .nm{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .txt .pct{font-weight:600;color:#a8a8b3}
.bar.gone{background:#0e0e11;border:1px dashed #33333b;cursor:default}.bar.gone .txt{color:#8e8e99}
.bar ha-icon{--mdc-icon-size:22px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px}
.card{border-radius:22px;background:#0e0e11;border:1px solid #1f1f25;padding:16px;display:flex;flex-direction:column;gap:10px}
.art{width:110px;height:110px;flex:none;border-radius:16px;background:linear-gradient(135deg,#5a3a1f,#ffb347);background-size:cover;background-position:center}
.cam{position:relative;min-height:190px;padding:0;overflow:hidden;background:#0a0d10}
.cam img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.cam span{position:absolute;left:12px;top:12px;padding:4px 10px;border-radius:10px;background:rgba(0,0,0,.6);font-size:12px}
@media(max-width:820px){
  .focus{grid-template-columns:1fr}
  .list{position:static;flex-direction:row;overflow-x:auto;max-height:none;padding:8px;gap:6px}
  .list .head,.list .foot{display:none}
  .ritem{min-height:48px;flex:none}.ritem .nm small{display:none}
  .hero h2{font-size:26px}
}
`;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const UNPLACED = "_not_in_a_room";
const OVERVIEW = "_overview";
const FAV_DOMAINS = ["light", "switch", "fan", "climate", "cover", "media_player", "lock", "vacuum", "scene", "script", "button", "input_boolean", "automation", "remote", "alarm_control_panel", "camera", "sensor", "binary_sensor", "weather"];
const UNPLACED_DOMAINS = ["light", "switch", "fan", "climate", "cover", "media_player", "lock", "vacuum", "remote", "alarm_control_panel"];
const DOMAINS = ["light", "switch", "fan", "climate", "cover", "media_player", "lock", "vacuum", "scene", "sensor", "binary_sensor", "camera", "remote", "alarm_control_panel"];
const MOTION = ["motion", "occupancy", "presence"];
// Anything that can be added to a room by hand in edit mode.
const ADDABLE = ["light", "switch", "fan", "climate", "cover", "media_player", "lock", "vacuum", "scene", "script", "button", "input_boolean", "automation", "sensor", "remote", "alarm_control_panel"];
const ON = new Set(["on", "open", "opening", "playing", "cleaning", "heat", "cool", "heat_cool", "auto", "dry", "fan_only", "unlocked"]);
const MODES = ["off", "cool", "heat", "heat_cool", "auto", "dry", "fan_only"];
const MODE_WORD = { off: "Off", cool: "Cool", heat: "Heat", heat_cool: "Auto", auto: "Auto", dry: "Dry", fan_only: "Fan" };

class GhostRoomsCard extends HTMLElement {
  setConfig(config) {
    this._config = { title: "Rooms", exclude_areas: [], names: {}, icons: {}, hidden: [], order: [], extra: {}, favourites: [], ...config };
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
    const borrowed = new Set(Object.values(cfg.extra || {}).flat());
    rooms[UNPLACED] = { id: UNPLACED, haName: "Not in a room", name: "Not in a room", icon: "mdi:map-marker-question-outline", hidden: false, items: [], unplaced: true };
    Object.values(h.entities || {}).forEach((e) => {
      if (e.hidden || e.entity_category || e.disabled_by) return;
      const domain = e.entity_id.split(".")[0];
      if (!DOMAINS.includes(domain)) return;
      if (domain === "switch" && e.device_id && players.has(e.device_id)) return;
      let area = e.area_id || devArea[e.device_id];
      const st = h.states[e.entity_id];
      if (!st) return;
      if (!area && UNPLACED_DOMAINS.includes(domain) && !borrowed.has(e.entity_id)) area = UNPLACED;
      if (!area || !rooms[area]) return;
      if (domain === "sensor" && !["temperature", "humidity"].includes(st.attributes.device_class)) return;
      if (domain === "binary_sensor" && !MOTION.includes(st.attributes.device_class)) return;
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
    Object.values(rooms).forEach((r) => { const seen = new Set();
      r.items = r.items.filter((s) => { if (!s.entity_id.startsWith("vacuum.")) return true;   // one robot, two integrations
        const k = s.attributes.friendly_name || s.entity_id;
        if (seen.has(k)) return false; seen.add(k); return true; }); });
    const controls = (r) => r.items.some((s) => !["binary_sensor", "sensor"].includes(s.entity_id.split(".")[0]));
    return Object.values(rooms).filter((r) => controls(r) && (this._edit || !r.hidden))
      .sort((a, b) => (a.unplaced ? 1 : 0) - (b.unplaced ? 1 : 0) || rank(a) - rank(b) || a.name.localeCompare(b.name));
  }
  _signature() {
    if (!this._hass) return "";
    const parts = [this._room || "", new Date().getHours()];
    (this._config.favourites || []).forEach((id) => { const s = this._hass.states[id]; if (s) parts.push(id, s.state, s.attributes.brightness, s.attributes.temperature, s.attributes.media_title, s.attributes.volume_level, s.attributes.unit_of_measurement); });
    const w = Object.values(this._hass.states).find((x) => x.entity_id.startsWith("weather.")); if (w) parts.push(w.state, w.attributes.temperature);
    this._rooms().forEach((r) => parts.push("#" + r.id) && r.items.forEach((s) => parts.push(s.entity_id, s.state, s.attributes.brightness, s.attributes.temperature,
      s.attributes.current_temperature, s.attributes.media_title, s.attributes.volume_level, s.attributes.hvac_action, s.attributes.entity_picture && !s.entity_id.startsWith("camera.") ? s.attributes.entity_picture : "")));
    return parts.join("|");
  }
  _haName(st, room) {
    let n = st.attributes.friendly_name || st.entity_id;
    // Multi-gang wall switches often come through as "Channel 1 Switch": the entity id (laundry_switch_1) says more.
    if (/^channel \d/i.test(n)) { const id = st.entity_id.split(".")[1].replace(/_/g, " "); n = id.charAt(0).toUpperCase() + id.slice(1); }
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
    if (d === "alarm_control_panel") { this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: id }, bubbles: true, composed: true })); return; }
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
    clearInterval(this._camTimer);
    if (!this._edit && !this._room) {
      let saved = null; try { saved = localStorage.getItem("ghost-rooms-room"); } catch (e) { /* private mode */ }
      this._room = saved === OVERVIEW || !rooms.some((r) => r.id === saved) ? OVERVIEW : saved;
    }
    const ov = this._overviewRoom();
    const room = this._room === OVERVIEW ? ov : this._room && rooms.find((r) => r.id === this._room);
    if (this._room && !room) this._room = this._edit ? null : OVERVIEW;
    const sel = room || (this._edit ? null : ov);
    const body = this._edit ? (room === ov ? this._editFavs() : room ? this._editRoom(room) : this._editGrid(rooms))
      : this._focusView(rooms, sel);
    this.shadowRoot.innerHTML = `<style>${CSS}</style><ha-card>${body}</ha-card>`;
    if (this._edit) { if (room === ov) this._wireFavs(); else this._wireEdit(rooms, room); }
    else { this._wire(rooms, sel); this._wireFocus(rooms, sel); }
  }

  /* ── R2: room list + big room panel ─────────────────────────────────── */
  _motion(room) { return this._of(room, "binary_sensor").some((s) => s.state === "on"); }
  _focusView(rooms, room) {
    const lightsOn = rooms.flatMap((r) => this._of(r, "light")).filter((s) => s.state === "on").length;
    const list = rooms.map((r) => {
      const s = this._summary(r), c = this._climate(r);
      const line = s.on ? s.text : ["All off", c.temp].filter(Boolean).join(" · ");
      return `<button class="ritem ${s.on ? "on" : ""} ${r.id === room.id ? "sel" : ""}" data-pick="${esc(r.id)}" aria-current="${r.id === room.id}" aria-label="${esc(r.name)}: ${esc(line)}">
        <span class="ic"><ha-icon icon="${esc(r.icon)}"></ha-icon></span>
        <span class="nm"><b>${esc(r.name)}</b><small>${esc(line)}</small></span>
        ${this._motion(r) ? `<span class="dot" title="Movement now" aria-label="Movement now"></span>` : ""}</button>`;
    }).join("");
    return `<div class="focus">
      <nav class="list" aria-label="Rooms">
        <div class="head"><b>${esc(this._config.title)}</b><span>${lightsOn ? `${lightsOn} light${lightsOn > 1 ? "s" : ""} on` : "All lights off"}</span></div>
        <button class="ritem ov ${room.id === OVERVIEW ? "sel" : ""}" data-pick="${OVERVIEW}" aria-current="${room.id === OVERVIEW}" aria-label="Overview: favourites and what's on">
          <span class="ic"><ha-icon icon="mdi:view-dashboard-outline"></ha-icon></span>
          <span class="nm"><b>Overview</b><small>${(this._config.favourites || []).length ? `${this._config.favourites.length} favourite${this._config.favourites.length > 1 ? "s" : ""}` : "Your favourites"}</small></span></button>
        <div class="sep" role="presentation"></div>
        ${list}
        <div class="foot">
          ${lightsOn ? `<button class="pill" data-act="all-off"><ha-icon icon="mdi:lightbulb-group-off-outline"></ha-icon>All off</button>` : ""}
          ${this._canEdit() ? `<button class="pill" data-act="edit-all" aria-label="Edit rooms: rename, hide, reorder"><ha-icon icon="mdi:pencil"></ha-icon>Edit</button>` : ""}
        </div>
      </nav>
      <main class="panel">${room.id === OVERVIEW ? this._overview(rooms) : this._panel(room, rooms.indexOf(room))}</main>
    </div>`;
  }
  _overviewRoom() {
    const items = (this._config.favourites || []).map((id) => this._hass.states[id]).filter(Boolean);
    return { id: OVERVIEW, name: "Overview", haName: "Overview", icon: "mdi:view-dashboard-outline", items };
  }
  /** The room a device lives in, so its name drops the room prefix only where that still reads well. */
  _homeOf(id, rooms) { return rooms.find((r) => r.items.some((s) => s.entity_id === id)) || null; }
  _overview(rooms) {
    const h = this._hass, hour = new Date().getHours();
    const hello = hour < 5 ? "Good night" : hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
    const all = rooms.flatMap((r) => r.items.map((s) => ({ s, r })));
    const lightsOn = all.filter(({ s }) => s.entity_id.startsWith("light.") && s.state === "on");
    const switchesOn = all.filter(({ s }) => /^(switch|fan|remote)\./.test(s.entity_id) && s.state === "on");
    const playing = all.filter(({ s }) => s.entity_id.startsWith("media_player.") && s.state === "playing");
    const w = Object.values(h.states).find((x) => x.entity_id.startsWith("weather."));
    const cond = w ? (w.state || "").replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase()) : "";
    const outside = w && w.attributes.temperature != null ? `${Math.round(w.attributes.temperature)}° outside · ${cond}` : "";
    const bits = [lightsOn.length ? `${lightsOn.length} light${lightsOn.length > 1 ? "s" : ""} on` : "All lights off",
      switchesOn.length ? `${switchesOn.length} switch${switchesOn.length > 1 ? "es" : ""} on` : "", playing.length ? `${playing.length} playing` : ""].filter(Boolean);
    const date = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
    const favs = (this._config.favourites || []).map((id) => h.states[id]).filter(Boolean);
    const bars = [], cards = [];
    favs.forEach((s) => {
      const d = s.entity_id.split(".")[0], r = this._homeOf(s.entity_id, rooms);
      if (d === "light") bars.push(this._lightBar(s, r));
      else if (["switch", "remote", "fan", "input_boolean", "script", "button", "automation"].includes(d)) bars.push(this._toggleBar(s, r));
      else if (d === "scene") bars.push(`<div class="bar" role="button" tabindex="0" data-toggle="${esc(s.entity_id)}" aria-label="${esc(this._name(s, r))}, run scene"><span class="txt"><ha-icon icon="${esc(s.attributes.icon || "mdi:palette-outline")}"></ha-icon><span class="nm">${esc(this._name(s, r))}</span><span class="pct">Run</span></span></div>`);
      else if (d === "media_player") cards.push(this._mediaCard(s, r));
      else if (d === "climate") cards.push(this._climateTile(s, r).replace('class="tile wide climate"', 'class="card climate"'));
      else if (d === "camera") cards.push(`<div class="card cam"><img data-cam="${esc(s.entity_id)}" alt="${esc(this._name(s, r))}, latest snapshot"><span>${esc(this._name(s, r))}</span></div>`);
      else if (d === "sensor" || d === "binary_sensor" || d === "weather") cards.push(this._readout(s, r));
      else cards.push(this._simpleTile(s, r));
    });
    const live = [...lightsOn.map(({ s, r }) => this._lightBar(s, null)), ...switchesOn.map(({ s }) => this._toggleBar(s, null))].slice(0, 12);
    return `
      <section class="hero ovh" aria-label="Overview"><ha-icon class="mark" icon="mdi:home-heart" aria-hidden="true"></ha-icon><div class="in">
        <div class="row1"><h2>${esc(hello)}<div class="meta">${esc(date)}${outside ? ` · ${esc(outside)}` : ""}</div></h2>
          ${lightsOn.length ? `<button class="glass" data-act="all-off">All lights off</button>` : ""}
          ${this._canEdit() ? `<button class="glass round" data-act="edit-room" aria-label="Edit favourites"><ha-icon icon="mdi:pencil"></ha-icon></button>` : ""}</div>
        <div class="chips">${bits.map((b) => `<span>${esc(b)}</span>`).join("")}</div>
      </div></section>
      <div class="lbl">Favourites</div>
      ${favs.length ? `${bars.length ? `<div class="bars">${bars.join("")}</div>` : ""}${cards.length ? `<div class="cards">${cards.join("")}</div>` : ""}`
        : `<div class="card favempty"><ha-icon icon="mdi:star-outline"></ha-icon><div><b>Pin what you use most</b><small>Lights, switches, the TV, a camera, the temperature: any device can be a favourite.</small></div>
           ${this._canEdit() ? `<button class="pill amber" data-act="edit-room"><ha-icon icon="mdi:plus"></ha-icon>Add favourites</button>` : ""}</div>`}
      ${live.length ? `<div class="lbl">On right now</div><div class="bars">${live.join("")}</div>` : ""}`;
  }
  _readout(s, room) {
    const d = s.entity_id.split(".")[0], a = s.attributes, name = this._name(s, room);
    let big = { unavailable: "Offline", unknown: "—" }[s.state] || s.state, small = name, icon = a.icon || "mdi:gauge";
    if (d === "weather") { big = a.temperature != null ? `${Math.round(a.temperature)}°` : "—"; small = `${name} · ${(s.state || "").replace(/-/g, " ")}`; icon = "mdi:weather-partly-cloudy"; }
    else if (d === "binary_sensor") { const dc = a.device_class; big = s.state === "on" ? ({ door: "Open", window: "Open", garage_door: "Open", motion: "Movement", occupancy: "Occupied", moisture: "Wet", smoke: "Smoke!" }[dc] || "On") : ({ door: "Closed", window: "Closed", garage_door: "Closed", motion: "Clear", occupancy: "Clear", moisture: "Dry", smoke: "Clear" }[dc] || "Off");
      icon = a.icon || ({ door: "mdi:door", window: "mdi:window-closed-variant", motion: "mdi:motion-sensor", garage_door: "mdi:garage" }[dc] || "mdi:checkbox-blank-circle-outline"); }
    else { const n = parseFloat(s.state); if (!isNaN(n)) big = `${Math.round(n * 10) / 10}${a.unit_of_measurement ? (a.unit_of_measurement === "°C" || a.unit_of_measurement === "°F" ? "°" : " " + a.unit_of_measurement) : ""}`;
      icon = a.icon || ({ temperature: "mdi:thermometer", humidity: "mdi:water-percent", power: "mdi:flash", energy: "mdi:lightning-bolt", battery: "mdi:battery" }[a.device_class] || "mdi:gauge"); }
    return `<div class="card readout" role="group" aria-label="${esc(name)}: ${esc(big)}"><ha-icon icon="${esc(icon)}" aria-hidden="true"></ha-icon><div><div class="big">${esc(big)}</div><small>${esc(small)}</small></div></div>`;
  }
  _editFavs() {
    const favs = this._draft.favourites;
    return this._editTop("Edit favourites", "Add any device to the Overview. Arrows change the order; lights and switches show as bars, everything else as cards.") +
      `<div class="tiles">
      <div class="tile edit add">
        <span class="label"><ha-icon icon="mdi:star-plus-outline"></ha-icon> Add a favourite</span>
        <input type="text" data-search placeholder="Search lights, TV, camera, temperature…" aria-label="Search for a device to add to favourites" value="${esc(this._query || "")}">
        <div class="results" data-results></div>
      </div>
      ${favs.map((id, i) => { const s = this._hass.states[id], nm = s ? this._haName(s, null) : id;
        return `<div class="tile edit">
        <span class="row"><span class="state">${esc(id.split(".")[0].replace("_", " "))}${s ? "" : " · missing"}</span>
          <span class="ctl"><button class="mode" data-fmove="${i}|-1" aria-label="Move ${esc(nm)} earlier" ${i === 0 ? "disabled" : ""}>◀</button>
          <button class="mode" data-fmove="${i}|1" aria-label="Move ${esc(nm)} later" ${i === favs.length - 1 ? "disabled" : ""}>▶</button>
          <button class="mode" data-funfav="${esc(id)}" aria-label="Remove ${esc(nm)} from favourites"><ha-icon icon="mdi:star-off-outline"></ha-icon></button></span></span>
        <span class="label">${esc(nm)}</span></div>`; }).join("")}</div>`;
  }
  _wireFavs() {
    const root = this.shadowRoot, f = this._draft.favourites, redraw = () => { this._sig = null; this._render(); };
    root.querySelectorAll("[data-fmove]").forEach((b) => b.addEventListener("click", () => {
      const [i, dir] = b.dataset.fmove.split("|").map((x) => parseInt(x, 10)), j = i + dir;
      if (j < 0 || j >= f.length) return; [f[i], f[j]] = [f[j], f[i]]; redraw();
    }));
    root.querySelectorAll("[data-funfav]").forEach((b) => b.addEventListener("click", () => { const i = f.indexOf(b.dataset.funfav); if (i >= 0) f.splice(i, 1); redraw(); }));
    const box = root.querySelector("[data-search]"), out = root.querySelector("[data-results]");
    const show = () => {
      this._query = box.value;
      const q = box.value.trim().toLowerCase(), have = new Set(f);
      if (q.length < 2) { out.innerHTML = q ? "" : `<small style="color:#8e8e99">Type at least two letters.</small>`; return; }
      const hits = Object.values(this._hass.states).filter((s) => FAV_DOMAINS.includes(s.entity_id.split(".")[0]) && !have.has(s.entity_id) &&
        ((s.attributes.friendly_name || "").toLowerCase().includes(q) || s.entity_id.includes(q)))
        .sort((a, b) => FAV_DOMAINS.indexOf(a.entity_id.split(".")[0]) - FAV_DOMAINS.indexOf(b.entity_id.split(".")[0])).slice(0, 12);
      out.innerHTML = hits.length ? hits.map((s) => `<button data-add="${esc(s.entity_id)}">${esc(this._haName(s, null))}<small>${esc(s.entity_id)}</small></button>`).join("")
        : `<small style="color:#8e8e99">Nothing matches “${esc(box.value.trim())}”.</small>`;
      out.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () => { f.push(b.dataset.add); this._query = ""; redraw(); }));
    };
    box.addEventListener("input", show); show();
    root.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", async () => {
      const act = b.dataset.edit;
      if (act === "back" || act === "cancel") { this._stopEdit(); return; }
      b.disabled = true; b.textContent = "Saving…";
      this._config = { ...this._saved, ...this._draft };
      try { await this._save(); this._saved = { ...this._saved, ...this._draft }; this._stopEdit(); }
      catch (e) { b.disabled = false; b.textContent = "Save"; alert("Couldn't save: " + (e && e.message ? e.message : e)); }
    }));
  }
  _panel(room, i) {
    const c = this._climate(room), area = (this._hass.areas || {})[room.id] || {};
    const hues = ["#7a5a3a,#3b2a1d", "#3a4a5c,#1a2230", "#4a3550,#221828", "#2f4a2a,#172414", "#5a3a52,#2a1b27", "#283a3a,#142020"];
    const bg = area.picture ? `background-image:url('${esc(area.picture)}')`
      : `background:radial-gradient(80% 120% at 75% 10%, ${hues[i % hues.length].split(",")[0]} 0%, ${hues[i % hues.length].split(",")[1]} 45%, #0b0a09 100%)`;
    const moved = this._motion(room);
    const meta = [c.temp, c.humidity, moved ? "movement now" : ""].filter(Boolean).join(" · ");
    const lights = this._of(room, "light"), anyOn = lights.some((s) => s.state === "on");
    const toggles = ["switch", "remote", "input_boolean", "fan", "script", "button", "automation"].flatMap((d) => this._of(room, d));
    const scenes = this._of(room, "scene");
    const cards = [
      ...this._of(room, "media_player").map((s) => this._mediaCard(s, room)),
      ...this._of(room, "climate").map((s) => this._climateTile(s, room).replace('class="tile wide climate"', 'class="card climate"')),
      ...this._of(room, "camera").filter((s) => s.state !== "unavailable" && !/map/i.test(s.entity_id + " " + (s.attributes.friendly_name || ""))).slice(0, 1).map((s) => `<div class="card cam"><img data-cam="${esc(s.entity_id)}" alt="${esc(this._name(s, room))}, latest snapshot"><span>${esc(this._name(s, room))}</span></div>`),
      ...["alarm_control_panel", "cover", "lock", "vacuum"].flatMap((d) => this._of(room, d)).map((s) => this._simpleTile(s, room)),
    ];
    return `
      <section class="hero" style="${bg}" aria-label="${esc(room.name)}">${area.picture ? "" : `<ha-icon class="mark" icon="${esc(room.icon)}" aria-hidden="true"></ha-icon>`}<div class="in">
        <div class="row1"><h2>${esc(room.name)}${meta ? `<div class="meta">${esc(meta)}</div>` : ""}</h2>
          ${anyOn ? `<button class="glass" data-act="room-off">Lights off</button>` : ""}
          ${this._canEdit() ? `<button class="glass round" data-act="edit-room" aria-label="Edit ${esc(room.name)}"><ha-icon icon="mdi:pencil"></ha-icon></button>` : ""}</div>
        ${scenes.length ? `<div class="scenes">${scenes.map((s) => `<button data-scene="${esc(s.entity_id)}">${esc(this._name(s, room))}</button>`).join("")}</div>` : ""}
      </div></section>
      ${lights.length ? `<div class="lbl">Lights · tap to switch, drag across to dim</div><div class="bars">${lights.map((s) => this._lightBar(s, room)).join("")}</div>` : ""}
      ${toggles.length ? `<div class="lbl">Switches</div><div class="bars">${toggles.map((s) => this._toggleBar(s, room)).join("")}</div>` : ""}
      ${cards.length ? `<div class="cards">${cards.join("")}</div>` : ""}
      ${!lights.length && !toggles.length && !cards.length ? `<div class="empty">Nothing to control in this room yet.</div>` : ""}`;
  }
  _lightBar(s, room) {
    const name = this._name(s, room);
    if (s.state === "unavailable") return `<div class="bar gone" aria-label="${esc(name)}, offline"><span class="txt"><ha-icon icon="mdi:lightbulb-off-outline"></ha-icon><span class="nm">${esc(name)}</span><span class="pct">Offline</span></span></div>`;
    const on = s.state === "on", dims = (s.attributes.supported_color_modes || []).some((m) => m !== "onoff");
    const pct = on ? (s.attributes.brightness != null ? Math.max(1, Math.round(s.attributes.brightness / 2.55)) : 100) : 0;
    const rgb = s.attributes.rgb_color, tint = on && rgb && s.attributes.color_mode && !["color_temp", "brightness", "onoff"].includes(s.attributes.color_mode)
      ? `background:linear-gradient(90deg, rgb(${rgb.join(",")}), rgba(${rgb.join(",")},.75))` : "";
    return `<div class="bar ${on ? "lit" : ""}" role="slider" tabindex="0" aria-label="${esc(name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"
        aria-valuetext="${on ? pct + "%" : "off"}" data-bar="${esc(s.entity_id)}" data-dims="${dims ? 1 : 0}">
      <span class="fill" style="width:${pct}%;${tint}"></span>
      <span class="txt"><ha-icon icon="${esc(s.attributes.icon || (on ? "mdi:lightbulb-on" : "mdi:lightbulb-outline"))}"></ha-icon><span class="nm">${esc(name)}</span><span class="pct">${on ? (dims ? pct + "%" : "On") : "Off"}</span></span>
    </div>`;
  }
  _toggleBar(s, room) {
    const name = this._name(s, room), d = s.entity_id.split(".")[0], on = s.state === "on";
    if (s.state === "unavailable") return `<div class="bar gone"><span class="txt"><ha-icon icon="mdi:power-plug-off-outline"></ha-icon><span class="nm">${esc(name)}</span><span class="pct">Offline</span></span></div>`;
    const icon = s.attributes.icon || { fan: "mdi:fan", script: "mdi:play-circle-outline", button: "mdi:gesture-tap-button", automation: "mdi:robot", remote: "mdi:remote-tv", alarm_control_panel: s.state === "disarmed" ? "mdi:shield-off-outline" : "mdi:shield-home" }[d] || (on ? "mdi:toggle-switch" : "mdi:toggle-switch-off-outline");
    const word = d === "script" || d === "button" ? "Run" : on ? "On" : "Off";
    return `<div class="bar ${on ? "lit full" : ""}" role="button" tabindex="0" data-toggle="${esc(s.entity_id)}" aria-label="${esc(name)}, ${esc(word)}">
      ${on ? `<span class="fill"></span>` : ""}<span class="txt"><ha-icon icon="${esc(icon)}"></ha-icon><span class="nm">${esc(name)}</span><span class="pct">${esc(word)}</span></span></div>`;
  }
  _mediaCard(s, room) {
    const a = s.attributes, playing = s.state === "playing";
    const what = a.media_title ? [a.media_title, a.media_artist].filter(Boolean).join(" — ") : { off: "Off", idle: "Idle", paused: "Paused", standby: "Standby", unavailable: "Unavailable" }[s.state] || s.state;
    return `<div class="card media" style="flex-direction:row;align-items:center;gap:16px">
      <div class="art" style="${a.entity_picture ? `background-image:url('${esc(a.entity_picture)}')` : ""}" aria-hidden="true"></div>
      <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:8px">
        <div class="state" style="font-size:12px;color:#a8a8b3;letter-spacing:.06em;text-transform:uppercase">${esc(this._name(s, room))}</div>
        <div style="font-size:17px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(what)}</div>
        <span class="ctl">
          <button class="round" data-svc="media_player.media_previous_track|${esc(s.entity_id)}" aria-label="Previous"><ha-icon icon="mdi:skip-previous"></ha-icon></button>
          <button class="round" style="background:#ffb347;color:#000" data-svc="media_player.media_play_pause|${esc(s.entity_id)}" aria-label="${playing ? "Pause" : "Play"}"><ha-icon icon="${playing ? "mdi:pause" : "mdi:play"}"></ha-icon></button>
          <button class="round" data-svc="media_player.media_next_track|${esc(s.entity_id)}" aria-label="Next"><ha-icon icon="mdi:skip-next"></ha-icon></button>
        </span>
        ${a.volume_level != null ? `<input type="range" min="0" max="100" value="${Math.round(a.volume_level * 100)}" data-volume="${esc(s.entity_id)}" aria-label="Volume" style="accent-color:#ffb347;width:100%">` : ""}
      </div></div>`;
  }
  _wireFocus(rooms, room) {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => {
      this._room = b.dataset.pick; try { localStorage.setItem("ghost-rooms-room", this._room); } catch (e) { /* ignore */ }
      this._sig = null; this._render();
    }));
    root.querySelectorAll("[data-act='edit-all']").forEach((b) => b.addEventListener("click", () => { this._focusRoom = this._room; this._room = null; this._startEdit(); }));
    root.querySelectorAll("[data-act='edit-room']").forEach((b) => b.addEventListener("click", () => { this._focusRoom = this._room; this._startEdit(); }));
    // Light bars: tap toggles; drag across sets brightness (fill follows the finger).
    root.querySelectorAll("[data-bar]").forEach((bar) => {
      const id = bar.dataset.bar, dims = bar.dataset.dims === "1", fill = bar.querySelector(".fill"), pct = bar.querySelector(".pct");
      let x0 = 0, moved = false, value = 0, down = false;
      const at = (e) => { const r = bar.getBoundingClientRect(); return Math.max(1, Math.min(100, Math.round(((e.clientX - r.left) / r.width) * 100))); };
      bar.addEventListener("pointerdown", (e) => { down = true; moved = false; x0 = e.clientX; this._dragging = true; bar.setPointerCapture(e.pointerId); });
      bar.addEventListener("pointermove", (e) => {
        if (!down || !dims) return;
        if (!moved && Math.abs(e.clientX - x0) < 8) return;
        moved = true; value = at(e); fill.style.width = value + "%"; pct.textContent = value + "%"; bar.classList.add("lit");
      });
      const end = () => {
        if (!down) return; down = false; this._dragging = false;
        if (moved) this._call("light", "turn_on", { entity_id: id, brightness_pct: value });
        else this._call("light", "toggle", { entity_id: id });
        if (this._pending) { this._pending = false; this._sig = null; setTimeout(() => (this.hass = this._hass), 50); }
      };
      bar.addEventListener("pointerup", end); bar.addEventListener("pointercancel", () => { down = false; this._dragging = false; });
      bar.addEventListener("keydown", (e) => {
        const st = this._hass.states[id]; if (!st) return;
        const now = st.state === "on" ? Math.round((st.attributes.brightness || 255) / 2.55) : 0;
        if (e.key === "ArrowRight" || e.key === "ArrowUp") { e.preventDefault(); this._call("light", "turn_on", { entity_id: id, brightness_pct: Math.min(100, now + 10) }); }
        if (e.key === "ArrowLeft" || e.key === "ArrowDown") { e.preventDefault(); now <= 10 ? this._call("light", "turn_off", { entity_id: id }) : this._call("light", "turn_on", { entity_id: id, brightness_pct: now - 10 }); }
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this._call("light", "toggle", { entity_id: id }); }
      });
    });
    root.querySelectorAll(".bar[data-toggle]").forEach((b) => b.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); b.click(); }
    }));
    // Camera snapshot, refreshed every 10 s while shown.
    const cams = [...root.querySelectorAll("[data-cam]")];
    const load = () => cams.forEach((img) => { const st = this._hass.states[img.dataset.cam]; const u = st && st.attributes.entity_picture;
      if (u) img.src = u + (u.includes("?") ? "&" : "?") + "t=" + Date.now(); });
    cams.forEach((img) => (img.onerror = () => { const card = img.closest(".cam"); if (card) card.style.display = "none"; }));
    if (cams.length) { load(); this._camTimer = setInterval(() => { if (this.isConnected && !document.hidden) load(); }, 10000); }
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
      button: "mdi:gesture-tap-button", input_boolean: on ? "mdi:toggle-switch" : "mdi:toggle-switch-off-outline", automation: "mdi:robot", remote: "mdi:remote-tv", alarm_control_panel: s.state === "disarmed" ? "mdi:shield-off-outline" : "mdi:shield-home" }[d] || "mdi:gesture-tap";
    const word = { locked: "Locked", unlocked: "Unlocked", open: "Open", closed: "Closed", cleaning: "Cleaning", docked: "Docked", returning: "Returning",
      on: "On", off: "Off", unavailable: "Unavailable", disarmed: "Disarmed", armed_home: "Armed home", armed_away: "Armed away", armed_night: "Armed night", triggered: "Triggered!", arming: "Arming" }[s.state] || s.state;
    const extra = d === "vacuum" ? `<span class="ctl">
        <button class="mode" data-svc="vacuum.start|${esc(s.entity_id)}">Clean</button>
        <button class="mode" data-svc="vacuum.return_to_base|${esc(s.entity_id)}">Dock</button></span>` : d === "cover" ? `<span class="ctl">
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
    this._draft = { names: { ...(c.names || {}) }, icons: { ...(c.icons || {}) }, hidden: [...(c.hidden || [])], order: [...(c.order || [])], extra, favourites: [...(c.favourites || [])] };
    this._saved = this._config;
    this._config = { ...c, ...this._draft };
    this._edit = true; this._sig = null; this._render();
  }
  _stopEdit() {
    this._edit = false; this._query = ""; this._config = this._saved || this._config; this._sig = null; this._pending = false;
    if (this._focusRoom) { this._room = this._focusRoom; this._focusRoom = null; }
    this._render();
  }
  _editTop(title, hint) {
    return `<div class="top">
        ${this._room && this._room !== OVERVIEW ? `<button class="pill back" data-edit="back"><ha-icon icon="mdi:chevron-left"></ha-icon>All rooms</button>` : ""}
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
    return this._editTop(`Edit ${room.name}`, "Rename a device, hide it, or pick the room it really lives in (that also updates its Area in Home Assistant). Leave a name empty to use Home Assistant's. Add any other device with the search box.") +
      `<div class="tiles">
      <div class="tile edit add">
        <span class="label"><ha-icon icon="mdi:plus-circle-outline"></ha-icon> Add a device</span>
        <input type="text" data-search placeholder="Search lights, switches, scripts…" aria-label="Search for a device to add to ${esc(room.name)}" value="${esc(this._query || "")}">
        <div class="results" data-results></div>
      </div>
      ${items.map((s) => `<div class="tile edit ${hidden.has(s.entity_id) ? "hid" : ""}">
        <span class="row"><span class="state">${esc(s.entity_id.split(".")[0].replace("_", " "))}${added.has(s.entity_id) ? " · added" : ""}</span>
          <span class="ctl"><button class="mode" data-fav="${esc(s.entity_id)}" aria-pressed="${this._draft.favourites.includes(s.entity_id)}" aria-label="${this._draft.favourites.includes(s.entity_id) ? "Remove from" : "Add to"} favourites: ${esc(this._haName(s, room))}"><ha-icon icon="${this._draft.favourites.includes(s.entity_id) ? "mdi:star" : "mdi:star-outline"}" style="${this._draft.favourites.includes(s.entity_id) ? "color:#ffb347" : ""}"></ha-icon></button>
          ${added.has(s.entity_id) ? `<button class="mode" data-remove="${esc(s.entity_id)}" aria-label="Remove ${esc(this._haName(s, room))} from ${esc(room.name)}">Remove</button>` : `<button class="mode" data-hide="${esc(s.entity_id)}" aria-label="${hidden.has(s.entity_id) ? "Show" : "Hide"} ${esc(this._haName(s, room))}"><ha-icon icon="${hidden.has(s.entity_id) ? "mdi:eye-off" : "mdi:eye"}"></ha-icon></button>`}</span></span>
        ${this._hass.entities[s.entity_id] && !added.has(s.entity_id) ? `<select data-area="${esc(s.entity_id)}" aria-label="Room for ${esc(this._haName(s, room))}">
          ${room.unplaced ? `<option value="">Choose a room…</option>` : ""}${this._areaOptions(room.unplaced ? "" : room.id)}</select>` : ""}
        <input type="text" data-name="${esc(s.entity_id)}" value="${esc((this._draft.names || {})[s.entity_id] || "")}" placeholder="${esc(this._haName(s, room))}" aria-label="Name for ${esc(this._haName(s, room))}">
      </div>`).join("")}</div>`;
  }
  _areaOptions(cur) {
    const skip = new Set(this._config.exclude_areas || []);
    return Object.values(this._hass.areas || {}).filter((a) => !skip.has(a.area_id)).sort((a, b) => a.name.localeCompare(b.name))
      .map((a) => `<option value="${esc(a.area_id)}" ${a.area_id === cur ? "selected" : ""}>${esc(a.name)}</option>`).join("");
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
        const useful = (s) => { const d = s.entity_id.split(".")[0];
          return ADDABLE.includes(d) && (d !== "sensor" || ["temperature", "humidity"].includes(s.attributes.device_class)); };
        const hits = Object.values(this._hass.states).filter((s) => useful(s) && !have.has(s.entity_id) &&
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
      root.querySelectorAll("[data-fav]").forEach((b) => b.addEventListener("click", () => {
        const f = this._draft.favourites, i = f.indexOf(b.dataset.fav); if (i < 0) f.push(b.dataset.fav); else f.splice(i, 1); redraw();
      }));
      root.querySelectorAll("[data-area]").forEach((sel) => sel.addEventListener("change", async () => {
        if (!sel.value) return;
        sel.disabled = true;
        try { await this._hass.callWS({ type: "config/entity_registry/update", entity_id: sel.dataset.area, area_id: sel.value }); }
        catch (e) { sel.disabled = false; alert("Couldn't move it: " + (e && e.message ? e.message : e)); return; }
        setTimeout(redraw, 400);
      }));
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
        node.favourites = [...new Set(d.favourites || [])];
        ["names", "icons", "extra"].forEach((k) => { if (!Object.keys(node[k]).length) delete node[k]; });
        ["hidden", "order", "favourites"].forEach((k) => { if (!node[k].length) delete node[k]; });
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
      if (act !== "room-off" && act !== "all-off") return;      // edit buttons have their own handlers
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
