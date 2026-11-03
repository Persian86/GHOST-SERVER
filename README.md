# 👻 Ghost Server

The home brain for Ghost. It runs in Docker on the **Asustor NAS** and every Ghost
agent connects to it: desktop, phone, tablet, a speaker in the kitchen.

Each device runs its own **Gemini Live** voice session. The server gives every
session the same identity, memory and rules, plus the household tools: **Claude**
reasoning, Home Assistant, Marquee, news and weather (Australia and Melbourne by default),
calendar, documents, multi-step tasks, and the ability for one device to use another
("ring my phone", "announce dinner on every device").

* **Admin page:** `http://192.168.80.244:8780`
* **Protocol for building agents:** [PROTOCOL.md](PROTOCOL.md)
* **Python SDK:** [`sdk/python/ghost_agent.py`](sdk/python/ghost_agent.py), with working examples in `sdk/python/examples/`

---

## Install on the Asustor

Ports 8000/8001 are taken by ADM and 8123 by Home Assistant, so Ghost Server uses **8780**.

1. **ADM → App Central**: install **Docker Engine** and **Portainer CE**.
2. **ADM → File Explorer**: create the folder `Docker/ghost-server`. It holds the database and the documents Ghost makes, so back it up.
3. Get the image. Pick one:
   * **From GitHub** (same flow as Marquee): push this folder to `github.com/Persian86/ghost-server`. The included workflow tests it and publishes `ghcr.io/persian86/ghost-server:latest`. Make the package public, or log Portainer in to ghcr.io.
   * **Build on the NAS:** in `docker-compose.yml`, comment out `image:` and uncomment `build: .`, then deploy from the folder.
4. **Portainer → Stacks → Add stack**: paste in `docker-compose.yml` and click **Deploy**.
5. Open `http://192.168.80.244:8780`, choose an admin password, then go to **Brain & keys** and fill in:
   * **Anthropic key**, used for Claude
   * **Gemini key**. Devices never see it; they get short-lived session tokens.
   * **Home Assistant**: a long-lived token
   * **Marquee**: an app key of kind *assistant*, made in Marquee → Settings → Connected apps
   * **Calendar**: your Google Calendar's *secret address in iCal format*

   Press each **Test** button.
6. Go to **Devices → New pairing code** and enter the code in your agent.

To update: Portainer → the stack → **Pull and redeploy**. Your data stays in the `Docker/ghost-server` folder.

## Try it without writing an agent

* Use the **Talk** tab on the admin page. It chats with Claude and every tool.
* Run the terminal agent: `python sdk/python/examples/text_agent.py http://192.168.80.244:8780`
* Run the voice agent (wear headphones): `python sdk/python/examples/voice_agent.py http://192.168.80.244:8780`

## Building your own agents

Read [PROTOCOL.md](PROTOCOL.md). In short:

1. Pair once and keep the device key.
2. Open the WebSocket `/api/v1/agent` and send `hello` with this device's **capabilities**, meaning its own tools.
3. For each voice session, call `GET /api/v1/session`, then connect Gemini Live with the returned token, instruction, and tools (the server's plus your own).
4. When Gemini calls a tool: if it's one of your capabilities, run it on the device; otherwise `POST /api/v1/tools/call`.
5. Post finished exchanges to `/api/v1/turns`, and speak whatever arrives as `say`.

Pieces worth reusing from **Ghost Desktop** (`F:\GITHUB\GHOST DESKTOP`) for a desktop agent:
- `core/wake_word.py` for the "Hey Ghost" wake word
- `core/echo.py` for talking over Ghost
- `ui.py` for the avatar and HUD
- `actions/` as the desktop capabilities

For the phone agent, `android/GhostDesktopLink.kt` shows the same connect-and-poll pattern in Kotlin.

## What's in the box

```
ghost_server/
  app.py          HTTP + WebSocket API, admin endpoints
  hub.py          live agent connections, device-to-device calls
  store.py        SQLite: settings, devices, memory, rules, turns, events, files
  prompt.py       the per-device Gemini Live instruction
  live.py         Gemini ephemeral session tokens
  chat.py         Claude text chat with tools
  claude.py       Claude client (prompt caching, routing)
  tools/          memory, brain, home, marquee, web, documents, devices, tasks
  static/admin.html
sdk/python/       ghost_agent.py + examples
tests/            end-to-end tests (real server, real SDK agents)
```

## Tests

```bash
pip install -r requirements.txt pytest
python -m pytest tests -q
```
