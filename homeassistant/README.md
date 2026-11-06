# Ghost for Home Assistant (Container edition)

This makes Ghost the brain behind Home Assistant's voice assistants. Every room screen
(ESP32-S3) talks to Ghost, and Ghost knows which room it's in. Ghost can also speak through
your Echos.

These steps are for **Home Assistant Container**, which runs in Docker on the Asustor and is
managed in Portainer. Container has **no Add-on store**. Wherever a normal guide says
"install the X add-on", here it means "run X as its own Docker container" (step 4).

You'll need:
* Ghost Server running at `http://192.168.80.244:8780`.
* Home Assistant at `http://192.168.80.244:8123`.
* This folder on your PC: `F:\GITHUB\GHOST SERVER\homeassistant\custom_components\ghost`.

---

## 1. Find Home Assistant's config folder on the NAS

Home Assistant keeps everything in one folder on the NAS, mapped to `/config` inside the
container.

1. Open **Portainer → Containers → homeassistant** (or whatever yours is called).
2. Scroll to **Volumes**. Find the line whose *container* path is `/config`. The *host*
   path next to it is the real folder, for example `/share/Docker/HomeAssistant/config`.
3. In **ADM → File Explorer**, that's the same folder: `Docker → HomeAssistant → config`.
   It should contain `configuration.yaml`.

## 2. Copy the Ghost integration in

1. In that config folder, make a folder called **`custom_components`** if it isn't there
   already. Use all lowercase and an underscore.
2. Copy the whole **`ghost`** folder from your PC into it. You can drag it into ADM File Explorer
   (Upload → Folder) or copy it over the network share (`\\192.168.80.244\Docker\HomeAssistant\config\`).

   It must end up like this:
   ```
   homeassistant/                 ← the folder with configuration.yaml
   └── custom_components/
       └── ghost/
           ├── __init__.py
           ├── config_flow.py
           ├── const.py
           ├── conversation.py
           ├── manifest.json
           ├── strings.json
           └── translations/en.json
   ```
   Don't put it one level too deep (`custom_components/ghost/ghost/…`). That is the most common
   mistake.
3. **Restart Home Assistant.** In HA, go to **Settings → System → ⋮ (top right) → Restart Home
   Assistant**, or restart the container in Portainer.

   After the restart, HA shows a warning in the log that a custom integration "has not been
   tested by Home Assistant". That's normal.

## 3. Pair Home Assistant with Ghost

1. Open Ghost's admin page (`http://192.168.80.244:8780`) and press **New pairing code**. The code
   lasts 10 minutes.
2. In Home Assistant: **Settings → Devices & services → + Add integration** and search **Ghost**.
   * Server: `http://192.168.80.244:8780`
   * Code: the 6 digits
   * Name: `Home Assistant`
3. **Approve it.** On Ghost's admin page, open the **Security** tab. "Home Assistant" is waiting
   for approval, so press **Approve**. Until you do, Ghost ignores it and HA's log says *approve
   this Home Assistant on the admin page*.

If **Ghost** doesn't appear in the search, the folder isn't in the right place (step 2) or HA
wasn't restarted. Hard-refresh the browser (Ctrl+F5) too.

## 4. Speech: Whisper and Piper as containers

Home Assistant needs something to turn speech into text (Whisper) and text into speech (Piper).
On Container they run as two small extra containers. In **Portainer → Stacks → + Add stack**,
name it `voice` and paste [`voice-compose.yml`](voice-compose.yml). Change the folder paths to
somewhere on your NAS, then **Deploy**. The first start downloads the models, which takes a few
minutes.

Then in Home Assistant:
1. **Settings → Devices & services → + Add integration → Wyoming Protocol**
   * Host `192.168.80.244`, port **10300**. That's Whisper (speech-to-text).
2. Add **Wyoming Protocol** again:
   * Host `192.168.80.244`, port **10200**. That's Piper (text-to-speech).

> No spare NAS power, or want better accuracy? **Home Assistant Cloud** (Nabu Casa, paid) does
> both speech jobs instead. Skip this step and pick "Home Assistant Cloud" in step 5.

## 5. Make "Ghost" the voice assistant

**Settings → Voice assistants → + Add assistant**
* Name: **Ghost**
* Language: English
* Conversation agent: **Ghost**
* Speech-to-text: **faster-whisper** (the Wyoming one from step 4)
* Text-to-speech: **piper**, with a voice you like
* Wake word: leave empty. The room screens listen for "Hey Ghost" themselves.

Press **⋮ → Set as preferred** so new screens use it automatically.

**Test it now** before any screens arrive. Press the **Assist** button (top right of the
dashboard) and type `what time is it?` or `turn on the kitchen light`. Ghost answers.

## 6. Rooms and speakers

* **Settings → Areas** should have your rooms (Bedroom, Office, Lounge, Kitchen…).
* Put every **Echo** and every **room screen** in its area: open the device, then the ✏️ pencil,
  then Area.
* After adding or moving speakers, reload Ghost: **Settings → Devices & services → Ghost → ⋮ →
  Reload**.

## 7. The room screens (when they arrive)

Flashing the ESP32 screens needs **ESPHome**, which is also a container on Container edition.
Add it to the same stack, or deploy it separately:
```yaml
  esphome:
    image: ghcr.io/esphome/esphome:stable
    container_name: esphome
    network_mode: host
    restart: unless-stopped
    volumes:
      - /share/Docker/esphome:/config
```
Open `http://192.168.80.244:6052` and copy the files from `GHOST SERVER\esphome\` into that
folder. Plug the first screen into your **PC** by USB and flash it from Chrome (ESPHome →
Install → "Plug into this computer"). After that, updates go over Wi-Fi. Home Assistant finds each
screen by itself (Settings → Devices & services → **Discovered → ESPHome → Configure**). Then:
1. Set the screen's **Area**.
2. In the screen's device page, set **Assistant** to **Ghost**.

---

## What you get
* "Turn the lights off" on the bedroom screen means the bedroom.
* Each room has its own short conversation history. Ghost's memory and tools are shared.
* Ghost can speak in a room ("tell the bedroom dinner's ready") or announce to the whole house.

## Amazon Echo speakers
If your Echos are in Home Assistant, through the built-in **Alexa Devices** integration or
**Alexa Media Player** (a HACS custom integration; on Container, install HACS by hand), Ghost
speaks through them too. Just put each Echo in its **Area** (step 6).
* "Tell the kitchen dinner's ready" plays on the kitchen Echo.
* "Announce to the house" plays in every room, once per room. A room with a Ghost screen uses the
  screen, not the Echo.
* Echos can't *listen* for Ghost: Amazon keeps their microphones to Alexa.

## Troubleshooting
| Problem | Fix |
|---|---|
| "Ghost" isn't in Add integration | Folder in the wrong place, or HA not restarted (step 2). Look in **Settings → System → Logs** for `ghost`. |
| "Failed to connect" when pairing | Check `http://192.168.80.244:8780` opens from the NAS. The code may have expired (10 min), or pairing is paused after wrong codes (wait 10 min). |
| Pairs, but Ghost never answers | Approve it on Ghost's **Security** tab (step 3). |
| Assist says "Sorry, I couldn't understand" | The voice assistant's conversation agent isn't **Ghost** (step 5). |
| Ghost speaks in the wrong room | The device or Echo isn't in the right **Area**. Reload Ghost after fixing it. |
| Whisper is slow | In `voice-compose.yml` use `--model tiny-int8`, or use Home Assistant Cloud. |
