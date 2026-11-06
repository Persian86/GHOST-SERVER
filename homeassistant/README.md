# Ghost for Home Assistant

Makes Ghost the brain behind Home Assistant's voice assistants, so every Assist
satellite (the ESP32 room screens, Voice PE, …) talks to Ghost and Ghost knows which room it's in.

## Install
1. Copy `custom_components/ghost` into Home Assistant's `/config/custom_components/`
   (File editor / Samba / SSH add-on), then restart Home Assistant.
2. On Ghost's admin page press **New pairing code**.
3. In Home Assistant: **Settings → Devices & services → Add integration → Ghost**, enter the
   server address (`http://192.168.80.244:8780`) and the code.
4. **Settings → Voice assistants → Add assistant** called "Ghost":
   * Conversation agent: **Ghost**
   * Speech-to-text: Whisper (add-on) or Home Assistant Cloud
   * Text-to-speech: Piper (add-on) or Home Assistant Cloud
   * Wake word: handled on the room screens themselves (microWakeWord)
5. Put each room screen in its **Area** (Bedroom, Office, …) and set its assistant to "Ghost".

## What you get
* "Turn the lights off" on the bedroom screen means the bedroom.
* Each room has its own short conversation history; Ghost's memory and tools are shared.
* Ghost can speak in a room: "tell the bedroom dinner's ready", or announce to the whole house.

## Amazon Echo speakers
If your Echos are in Home Assistant, through the built-in **Alexa Devices** integration or the
**Alexa Media Player** add-on, Ghost speaks through them too. No extra setup is needed. Just put
each Echo in its **Area**.
* "Tell the kitchen dinner's ready" plays on the kitchen Echo.
* "Announce to the house" plays in every room, once per room. A room with a Ghost screen uses the
  screen, not the Echo.
* After adding or moving speakers, reload the Ghost integration so Ghost learns the new rooms.
* Echos can't *listen* for Ghost: Amazon keeps their microphones to Alexa.
