# Ghost room screens (Waveshare ESP32-S3-Touch-LCD-4B)

Each screen is a Home Assistant voice satellite with its own wake word, plus a small
Ghost display: clock, weather, an animated Ghost "presence" that shows listening /
thinking / speaking, and three buttons for the room's lights.

## You need
* The board, a **3525 8Ω 2W speaker with an MX1.25 plug**, and a USB-C power supply
* The **ESPHome** add-on in Home Assistant (Settings → Add-ons → ESPHome Device Builder)
* The **Ghost** integration installed (see `../homeassistant/README.md`) and a voice assistant called "Ghost"

## Set up a screen
1. Copy this folder's files into the ESPHome add-on's folder (`/config/esphome/`).
2. Rename `secrets.example.yaml` to `secrets.yaml`. Put in your Wi-Fi details and one key per screen
   (`openssl rand -base64 32`, or let ESPHome make one).
3. Open `bedroom.yaml` and change the light entity ids to your real ones
   (Settings → Entities shows them, for example `light.bedroom_ceiling`).
4. Plug the board into the computer running the browser, then in ESPHome press **Install → Plug into
   this computer**. The first flash must be over USB. After that, updates go over Wi-Fi.
5. Home Assistant finds it: **Settings → Devices → ESPHome → Configure**. Put it in the **Bedroom** area.
6. On the device page set **Assistant → Ghost**, and you're done. Say "Hey Jarvis".

## Using it
* Wake word, or tap the glowing circle, or press the side **PWR** key: talk to Ghost
* Side **BOOT** key: microphone off/on (the circle turns red)
* The screen dims after 90 seconds and wakes on touch or the wake word

## If something looks off
* **Red and blue swapped:** set `color_order: BGR` under `display:` in `ghost-room-base.yaml`.
* **Screen stays black:** check the board's backlight entity ("Screen") is on, and look at the logs in ESPHome.
* **Ghost is too quiet or loud:** use the "Speaker" media player volume in Home Assistant.
* **Wake word misses you:** try `okay_nabu` (set both `wake_word` and `wake_word_name`).
