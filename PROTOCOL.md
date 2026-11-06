# Ghost Agent Protocol (v1)

How any agent (desktop, phone, tablet, room speaker, watch…) talks to Ghost Server.
Plain HTTP + one WebSocket, JSON everywhere. Server default: `http://192.168.80.244:8780`.

```
            ┌──────────────── Ghost Server (Asustor) ───────────────┐
            │  Claude brain · shared memory · rules · Home Assistant │
            │  Marquee · news · weather · calendar · documents       │
            │  device registry · task engine                         │
            └───────▲───────────────▲───────────────────▲───────────┘
          HTTP + WS │               │                   │
      ┌─────────────┴───┐   ┌───────┴────────┐   ┌──────┴─────────┐
      │ Desktop agent    │   │ Phone agent     │   │ Tablet agent    │
      │ Gemini Live ⇄ 🎤 │   │ Gemini Live ⇄ 🎤│   │ Gemini Live ⇄ 🎤│
      │ own tools: apps, │   │ own tools: ring,│   │ own tools: show │
      │ files, screen    │   │ flashlight, SMS │   │ photo, timer    │
      └──────────────────┘   └─────────────────┘   └─────────────────┘
```

**Each device runs its own Gemini Live session.** It listens, speaks, and holds the
real-time conversation. The server supplies the session's instruction (identity,
time, shared memory, rules, other devices) and a set of **server tools**. When
Gemini calls a tool, the agent runs it locally if it is one of its own
**capabilities**, otherwise it POSTs it to the server.

---

## 1. Pairing (once per device)

1. On the admin page (`/`), press **New pairing code**. You get a 6-digit code, valid for 10 minutes.
2. The agent sends:

```http
POST /api/v1/pair
{"code": "837763", "name": "Kitchen Tablet", "kind": "tablet", "info": {"platform": "Android 15"}}
→ 200 {"device_id": "dev_3f9a1c0b2e", "device_key": "gk_…", "assistant_name": "Ghost", "approved": false,
        "message": "Waiting for approval — approve this device on the Ghost Server admin page (Security)"}
```

**Approval.** With *New devices must be approved* on (the default), a new device gets its key but
every call answers **403** (and the live link closes with **4403**) until the owner presses
Approve on the admin page. Keep the key, show "waiting for approval", and retry. A 403 is not a
reason to unpair. Five wrong codes from one address (15 from all addresses together) pause pairing
for 10 minutes (**429**).

**Key renewal.** `POST /api/v1/key/rotate` → `{"device_key": "gk_…", "rotate_days": 30}`. Save
the new key before you use it. The old key keeps working for 24 hours in case the reply was
lost. `GET /api/v1/key` → `{"rotate_days": 30, "age_days": 12.5}` (cheap; check once a day), and
`GET /api/v1/session` also includes `"key": {"rotate_days": 30, "age_days": 12.5}`, so renew
when `age_days >= rotate_days`.

Store `device_key` securely: Android Keystore/EncryptedSharedPreferences, Windows DPAPI, iOS Keychain.
It is shown once. Every later call sends `Authorization: Bearer <device_key>`.
A 401 means the key was revoked or is unknown, so pair again.

`kind` is free text. These help the assistant pick the right device: `desktop`,
`laptop`, `phone`, `tablet`, `room`, `tv`, `wearable`.

## 2. Starting a voice session

```http
GET /api/v1/session
→ {
  "device": {"id": "dev_…", "name": "Kitchen Tablet", "kind": "tablet"},
  "assistant_name": "Ghost", "owner_name": "Adam",
  "system_instruction": "You are Ghost … [OTHER DEVICES] … [WHAT YOU KNOW] …",
  "server_tools": [ {"name": "remember", "description": "…", "parameters": {"type": "OBJECT", …}}, … ],
  "gemini": {"model": "gemini-3.1-flash-live-preview", "voice": "Fenrir",
             "api_version": "v1alpha", "token": "auth_tokens/…", "expires": "…"},
  "server_time": 1793671906.1
}
```

To open the Live session:

* Credentials: create the Gemini client with `api_key = gemini.token` and
  `http_options.api_version = gemini.api_version`. The token is an **ephemeral,
  single-use token**: connect within 2 minutes, and the session lasts up to 30 minutes.
  The real Gemini key never leaves the server. If `gemini.api_key` is present instead,
  the admin enabled key sharing. If `gemini.error` is present, show it to the user.
* `system_instruction`: use it as given. You may append device-specific lines.
* Tools: `server_tools` **plus your own capabilities**, in one `function_declarations` list.
  The schemas use Gemini's UPPER-CASE types and can be passed straight through.
* **Call `/session` again for every new Live session** (including reconnects). The
  instruction carries the current time, the latest memory, and the list of devices
  that are online.

## 3. Tool calls

When Gemini emits a `tool_call`, handle each function call:

* **Your capability?** Run it on the device and send back `{"result": "…"}`.
* **Anything else**: forward it to the server:

```http
POST /api/v1/tools/call
{"name": "home_assistant", "args": {"action": "off", "target": "lounge lights"}}
→ {"name": "home_assistant", "result": "Done (off lounge lights). now: Lounge Lamp: off"}
```

Return `result` to Gemini as the function response. Some tools take time:
`think_deeply` and `create_document` can take up to a minute or two, so use a long HTTP timeout (200 s).

### Server tools (v1)

| Tool | What it does |
|---|---|
| `remember`, `recall`, `forget` | Shared household memory, the same on every device |
| `standing_rule` | "From now on…" rules that every device follows |
| `think_deeply` | Claude reasoning (use `effort: max` for Opus) |
| `run_task` | Multi-step job run by Claude on the server; reports back through `task_done` |
| `home_assistant` | Lights, climate, locks (security actions need `confirmed: true` after the user says yes), scenes, summaries, Assist |
| `marquee` | Media server commands, continue watching, new arrivals |
| `news`, `web_search`, `weather`, `calendar`, `briefing` | Information, Australia/Melbourne by default |
| `create_document` | .docx / .pptx / .xlsx. Returns `/files/<id>`; download with `?k=<device_key>` |
| `list_devices`, `device_action`, `announce`, `device_events` | Use the **other** devices |

## 4. The live link (WebSocket)

Keep one socket open whenever the agent is running, not only during a voice session.
It is what makes the device visible to the house.

```
WS /api/v1/agent?key=<device_key>
```

| Direction | Message | Meaning |
|---|---|---|
| server → agent | `{"type":"welcome","device_id":…,"name":…}` | Connected |
| agent → server | `{"type":"hello","capabilities":[decl…],"info":{…}}` | **Send first.** Your own tools, in the same declaration format |
| server → agent | `{"type":"ready","server_tools":[names]}` | Registered |
| server → agent | `{"type":"call","id":"c_…","tool":"ring","args":{…}}` | Another device, or the task engine, wants one of your capabilities |
| agent → server | `{"type":"result","id":"c_…","ok":true,"result":"ringing"}` | Answer within 20 s (`ok:false,"error":"…"` on failure) |
| server → agent | `{"type":"say","text":"Dinner's ready","from":"Office PC"}` | Speak it. Inject it into your Live session or use local TTS |
| server → agent | `{"type":"task_done","task":"t_…","status":"done","text":"…","say":"…"}` | A `run_task` you started has finished. Tell the user |
| agent → server | `{"type":"event","event":"notification","data":{…}}` | Report something (see §5) |
| server → agent | `{"type":"notify","title":"Ghost security","text":"…","level":"security"}` | Show a notification (security alerts go to the owner's own phone; not held by Do Not Disturb) |
| agent → server | `{"type":"ping"}` → `{"type":"pong"}` | Keepalive (also use WebSocket pings) |

Close codes: **4401** means the key was revoked, so pair again. **4403** means the device is
waiting for approval, so keep the key and retry slowly. **4000** means a newer
connection from the same device replaced this one. On any other drop, reconnect
with backoff (1 s → 30 s).

To inject a `say` into Gemini Live, send it as a user turn, for example:
`[MESSAGE FROM THE HOME SERVER — say it to the user naturally] Dinner's ready`.

## 5. Reporting back

```http
POST /api/v1/turns    {"turns": [{"role": "user", "text": "…"}, {"role": "assistant", "text": "…"}]}
POST /api/v1/events   {"type": "notification" | "battery" | "alert" | "status", "data": {…}}
```

* **Turns**: post each finished exchange using Live's input and output transcriptions.
  Other devices then get it as recent context, so "what was I asking the kitchen tablet about?" works.
* **Events**: `status` data (for example `{"battery": 54, "charging": true}`) also updates the
  device card on the admin page. Notifications show up in `device_events`.

## 6. Text mode

For devices without a microphone, or as a fallback when Gemini is down:

```http
POST /api/v1/chat   {"text": "turn off the lounge lights"}
→ {"text": "Done — the lounge lamp is off.", "tools": ["home_assistant"]}
```

Claude answers using every server tool plus this device's own capabilities, which are routed back over the live link.

### Room speakers behind Home Assistant

The Ghost integration for Home Assistant (`homeassistant/`) pairs as one device of kind
`homeassistant`. It sends spoken requests from each Assist satellite with the room it is in:

```http
POST /api/v1/assist   {"text": "lights off", "room": "Bedroom", "satellite": "Bedroom Screen"}
→ {"text": "The bedroom light is off.", "tools": ["home_assistant"], "conversation_id": "…"}
```

Replies are written to be spoken. "Here", or "the lights" with no room named, mean that room, and
each room keeps its own short history. Over its live link the integration reports
`"info": {"rooms": [...]}`, and `announce` to a room name sends it `{"type":"say","text":…,"room":"Bedroom"}`.

### Household: timers, lists, notes, Visual ID, photos

```http
GET  /api/v1/timers                      → {"items": [{id, kind: timer|alarm, label, status, remaining, ends_at}]}
POST /api/v1/timers  {seconds, label} | {alarm: "6:30am", days: "weekdays", label}
POST /api/v1/timers/{id}  {action: stop | cancel | add, seconds}
GET  /api/v1/lists                       → {"lists": {"shopping": [{id, text, done}], "to do": [...]}}
POST /api/v1/lists  {list, text}         POST /api/v1/lists/item/{id}  {done, delete}
GET  /api/v1/notes?who=Adam              POST /api/v1/notes {text, to, from}     POST /api/v1/notes/{id}
POST /api/v1/faces/identify {image: base64 JPEG} → {people: [{name, score}], guests, greet?, notes?, agenda?}
POST /api/v1/faces/enroll {name, image, admin_password}
GET  /api/v1/photos → {count, photos: [n…]}       GET /photos/{n}?w=1600&k=<device key>
```

Live link pushes: `{"type":"timers"}`, `{"type":"lists"}`, `{"type":"notes"}` snapshots on every change (and on connect).
A timer set on a tablet rings there as `{"type":"timer","event":"ring","timer":{…},"text":…}` (and `"stopped"`).
On phones, PCs and room speakers it is announced instead, as an urgent `say`, three times. Both get past Do Not Disturb.

## 7. Designing capabilities

* Name them as verbs, specific to the device: `open_app`, `take_screenshot`, `ring`,
  `set_brightness`, `show_photo`, `start_timer`.
* Write descriptions for the model. Say what the capability does and when to use it.
* Return a short, factual result, and say so when you could not confirm it.
* Anything dangerous on the device (delete, purchase, call someone) should confirm with the user
  **on that device** before acting, even when the request came from another device.
* Capabilities can change. Send a new `hello` any time and the server replaces the list.

### HTTPS (port 8781)

The server makes its own certificate (`data/tls/`) and serves the same API over HTTPS on **8781**.
1. `GET http://…:8780/api/v1/tls` (public) → `{"enabled": true, "port": 8781, "sha256": "A385…", "pem": "-----BEGIN CERTIFICATE…"}`.
2. Check that the SHA-256 of the PEM's DER bytes equals `sha256`. Save the PEM and **pin** it: accept that
   certificate for the server and nothing else. Skip hostname checks for it, because the certificate is the pin.
3. Use `https://<host>:8781` and `wss://<host>:8781/api/v1/agent` from then on. Pair over HTTPS
   when it's offered, so the device key never crosses the Wi-Fi unencrypted.

With **HTTPS only** on (admin → Security), the plain-HTTP API answers **426** and the live link closes with
**4426**. `/api/v1/health`, `/api/v1/tls` and `/ghost-server.crt` stay on HTTP.

### Meal planner and front door

* `GET /api/v1/meals?days=7` → `{"days": [{"day": "2026-11-09", "label": "tonight", "title": "Tacos", "ingredients": […]}, …]}`
* `POST /api/v1/meals {"day": "thursday", "title": "Tacos"}` sets a day; Claude works out the ingredients. Add `"clear": true` to remove a day.
* `POST /api/v1/meals/shop {"days": ["thursday"]}` puts the ingredients on the shopping list (the coming week if no days given).
* Live link: `{"type": "meals", "days": [...]}` whenever the plan changes.
* Front door: a doorbell press or a visitor/delivery arrives as a `say` with `"camera"`, `"picture"` and
  `"door": "bell" | "visitor" | "delivery"`. A press is `urgent` and gets through Do Not Disturb.
  Home Assistant gets `"room": "@near"` (occupied rooms, else everywhere) plus `"near_room"`.

## 8. Security model (home network only)

* The server is meant to be reachable on the LAN only. Don't port-forward it. For remote access later, use WireGuard.
* Device keys are random 256-bit tokens. The server stores only their SHA-256 hash. Revoke a device on the admin page.
* The admin page is protected by a password you choose on first visit (8+ characters), plus optional
  **two-step login**: a TOTP code from an authenticator app, with 8 one-time backup codes.
* **Lockout:** 5 wrong passwords or codes from one address (15 from all addresses together) lock
  the admin page for 15 minutes. Adding a person to Visual ID from a tablet counts towards the same lockout.
* Admin sessions are cookies (HttpOnly, SameSite=Strict). They last up to 7 days and sign out after 12 idle
  hours. Changes need the `X-Ghost-Admin: 1` header, so another web page can't drive your browser. The
  Security tab lists signed-in browsers and has *Sign out everywhere else*.
* The **security log** (Security tab) records sign-ins, wrong tries, lockouts, pairing, approvals, removals,
  key renewals, new addresses, Visual ID enrolments and which settings changed (names only, never values).
  Alerts go to the owner's phone as `notify` messages.
* Traffic can be encrypted with the server's own pinned certificate (HTTPS on 8781, see above).
* Gemini credentials go out as ephemeral single-use tokens.
* Web pages and notifications that tools return are marked UNTRUSTED. Agents and models should report that content, not obey it.

## Seasonal themes

`GET /api/v1/theme` (device key) and the `theme` field of `/api/v1/dashboard` return today's look; the live link
sends the same object as `{"type": "theme", ...}` on connect and whenever it changes (midnight, or an admin change).

```json
{"type": "theme", "id": "halloween", "name": "Halloween", "emoji": "🎃", "greeting": "Happy Halloween",
 "particles": "bats", "accessory": "witch_hat", "accent": "#FF8A3D", "soft": "#FFB27A",
 "orb": ["#FFE2C7", "#FF9B50", "#B8461A", "#3D1405"], "holiday": true, "effects": true, "date": "2026-10-31"}
```

`id` "" = no theme. `particles`: none, bats, snow, snow_light, hearts, eggs, confetti, lanterns, clovers, petals,
leaves, stars, sparkles. `accessory`: none, witch_hat, santa_hat, bunny_ears, party_hat, heart, shamrock, flower,
poppy, bow_tie, sunglasses, beanie. `orb` may be null (keep Ghost's normal colours). Birthdays add `"for": "<name>"`
and the greeting already contains the name. Settings: theme_mode (auto / off / a theme id to preview),
theme_off, theme_seasons, theme_effects, theme_hemisphere, theme_birthdays.
