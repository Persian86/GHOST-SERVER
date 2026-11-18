# Ghost room prototype: Waveshare ESP32-S3-Touch-LCD-4B

This is a new native ESP-IDF application for the exact 16 MB flash / 8 MB PSRAM
4B board, with a small animated LVGL ghost, tap-to-talk, and streamed speech
through Ghost Server. It is not an Android app or an automatic AIChats setting.
The ESP-IDF firmware build is separate from the server's Python test job.

## What this first version does

- Tap to connect and record; tap Finish speaking to request a reply. Recording
  also ends after 19 seconds. The microphone is not sent while idle or speaking.
- Server connects to Gemini Live using its configured model, voice, instruction,
  memory and permitted server tools. The provider key never goes to the board.
- Mono signed 16-bit PCM at 16 kHz in both directions. The server resamples
  Gemini's 24 kHz reply with packet-boundary continuity.
- A cancelled/disconnected session cancels its live tasks; total session time,
  audio size, simultaneous sessions and messages are bounded. Approval,
  revocation and household access ceilings are checked during the session.
- No automatic emergency calls, background listening, room selection, camera
  viewing, multi-room arbitration or always-on announcement link in this version.
  Wake word and full duplex/echo cancellation are later stages.

## Server first

Deploy this change to Ghost Server before using the firmware. Existing phone,
tablet and desktop transports are unchanged. Configure Gemini voice on the
server as usual. The new WebSocket route is `/api/v1/room-voice`, authenticated
only by an Authorization Bearer device credential, never a URL query token.

The firmware prototype supports **HTTP/WS on a private LAN IPv4 address only**,
such as `http://192.168.80.244:8780`. It rejects public addresses and redirects.
Audio and the device credential are not encrypted on that LAN transport. Use a
trusted home network and do not expose this endpoint or the device to the
internet. A server configured HTTPS-only rejects it; do not turn off that policy
to accommodate the prototype. Private-CA certificate provisioning is not yet
implemented on this firmware.

## Backup and flash layout

Before any installation, read the entire original flash and verify it. The
owner's local backup on 18 November 2026 is 16,777,216 bytes, with SHA-256
`668ec53e283996fb2d021d6cfd3a8346a7ef2ef3f4d9d170bab364500473cad9`.
The private backup contains settings and must never be committed or uploaded.

`partitions.csv` matches that specific backup: factory Brookesia at `0x100000`,
5 MB; Xiaozhi in `ota_0` at `0x600000`, 6 MB; original NVS, models and storage
are separate. Do not assume another board has the same table.

The build artifact contains **the Ghost application only**, not an erase/flash
script or a replacement bootloader. Validate the built image's target, size,
checksum and partition compatibility before installing. A deliberate install
can replace `ota_0` and select it in the existing OTA metadata; this replaces the
Xiaozhi application in that slot. Do not write the app to address zero or erase
the board. Do not flash new partition tables or eFuses. The factory Brookesia
partition and the full private backup must remain available for recovery.

## First setup after installation

1. The screen shows a `Ghost-Setup-xxxx` hotspot and a random WPA2 password.
2. Connect a phone to that hotspot, then open `http://192.168.4.1`.
3. Enter home 2.4 GHz Wi-Fi credentials and the private Ghost Server address.
4. Generate a new six-digit pairing code in Ghost Server's admin page and enter
   it on the setup page. This is a Ghost code, not a Xiaozhi activation code.
5. The device pairs, stores its own device key locally, and restarts. Approve the
   device under Ghost Server Security if approval is required. The setup hotspot
   and web server do not remain running after a successful paired reboot.
6. Tap to talk, say a short phrase, tap Finish speaking, and check the voice.

On firmware 0.1.2 and later, tap **Setup** for the native Wi-Fi page. It shows
the current network and signal strength and scans nearby 2.4 GHz networks.
Tap a network, enter its password with the touchscreen keyboard and tap Connect.
Changing networks keeps the server address and device pairing. A blank password
keeps the saved password only when selecting the same secured network. Failed
changes restore the previous saved network; passwords are never displayed unless
the user explicitly selects Show password. No restart is needed on success.
Close/cancel an active voice turn before opening Wi-Fi settings.

First pairing still uses the password-protected setup hotspot and its web page.
If home Wi-Fi fails on startup, setup opens again. The old destructive long-hold
Setup shortcut was removed; opening Wi-Fi settings does not erase pairing.

## Verification and remaining hardware checks

Firmware 0.1.1 initializes the shared I2S bus explicitly at 16 kHz stereo before
constructing the codecs, rather than starting with the BSP's default 22.05 kHz
mono layout. Mono network packets are limited to one 20 ms frame per interval,
including when DMA has buffered old samples. USB warnings report only frame
counts, timing and error codes, never audio samples, transcripts, Wi-Fi details
or device credentials. Specific errors survive connection cleanup.

Firmware 0.1.2 adds a live microphone level bar while Listening, signal/level
metrics in USB diagnostics, and WebSocket close/transport error codes. It updates
the client to 1.8.0, enables separate transmit locking and increases TCP windows
to 16 KB. These transport changes are mitigations pending physical retesting,
not proof that the upload failure is resolved. The server has separately completed
generated-speech tests with both short and eight-second paced recordings. Actual
speaker testing is deferred until the owner's correct speakers arrive.

After the connection is confirmed stable, the next requested stage is smoother
character animation and hands-free voice activation. These are not enabled by
the network/microphone update.

Server tests cover the PCM protocol, resampling across arbitrary packet cuts,
input bounds, credential redaction, disconnected sessions and concurrency.
GitHub Actions compiles the actual target using ESP-IDF 5.5.2, BSP 2.0.0, LVGL
9.5.0, LVGL port 2.8.0~1 and WebSocket client 1.6.1. The resolved dependency lock
and source commit travel with the application artifact.

A successful build is not proof of working microphones, correct channel mapping,
speaker volume, display/touch timings or echo behavior. These require the
physical board. Also check cancel, silence, server outage, lost Wi-Fi, revocation,
large replies, repeated turns and return to setup. A restore from the private
backup has not yet been tested.
