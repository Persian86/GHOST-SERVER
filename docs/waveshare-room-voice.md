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

Firmware 0.1.5 adds **Speaker volume** above the Wi-Fi list in Setup. Drag
the slider from 0 (muted) to 100 and release to apply and save it. The initial
level is 70; a saved level is restored after restart. Volume lives in a separate
`ghost_audio` NVS namespace, leaving the Wi-Fi/pairing blob unchanged. Writes
occur on release, not on every movement. If applying or saving fails, the
previous level is restored and the screen reports the error. Adjust volume
between voice turns; speaker loudness and distortion still need physical tests.

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

## Experimental hands-free and animation (0.2.0)

Say **Hey Ghost**, pause until Listening appears, then ask a question. A
wake-started turn finishes after about 1.3 seconds of quiet following speech,
or after six seconds if no speech is detected. The existing 19-second limit and
Finish/Cancel buttons remain. Manually started turns still use Finish speaking.
The simple energy-based endpoint detector may need tuning for quiet speech,
background noise and different distances; it is not neural VAD.

Setup has a saved **Hey Ghost (experimental)** switch, enabled initially.
Idle audio is processed locally, not sent to Ghost Server or a cloud provider.
Only a detected phrase starts the normal authenticated voice connection.
Detection pauses in Setup, during a turn and for 2.5 seconds after a turn.
The microphone has one owning task, avoiding simultaneous codec reads.

This uses Espressif ESP-SR 2.1.3's English MultiNet7 phrase recognition with a
single `hey ghost` command. It is **not a custom-trained
WakeNet model** and is not claimed to match a dedicated wake-word detector's
false-trigger performance. Test nearby speech/TV, different voices, distances,
quiet requests and repeated replies. Tap-to-talk remains available if the model
cannot initialize. No always-on streaming is used as a fallback.

The 2.75 MB English model is packed into the application; the original model
partition, factory application, storage and partition table remain untouched.
Never use generic ESP-IDF flash targets: ESP-SR adds a separate model flash
target that is not part of Ghost's reviewed application-only installation.

Animation adds slow sideways drift, separately moving hem/drapes, irregular
blinks and small eye glances. Mouth opening follows decoded playback RMS with
attack/release smoothing rather than an unrelated sine wave. This is amplitude
animation, not phoneme lip-sync. The hidden character stops redrawing in Setup.
Actual frame rate, recognition latency/accuracy, echo rejection and peak memory
usage require tests on the physical board alongside Wi-Fi audio uploads.

Version 0.2.0 must not be installed: its physical test produced a startup
LoadProhibited panic inside MultiNet7 model construction. The matching decoded
trace passes through `sprintf` and `model_create` into `ghost_wake_init`.
Version 0.2.1 includes the separate `fst` command-dictionary bundle that
MultiNet7 requires in addition to `mn7_en`. Without `fst`, the constructor
formats `%s/%s/vocab` using a NULL filesystem base path. The matching
disassembly confirms this fallback; the omitted dictionary is also explicitly
included by Espressif's standard `movemodel.py` packager. A build-time parser
now requires the English model, vocabulary, command dictionary and license,
and rejects invalid bounds/duplicate entries. Runtime also checks for `fst`
before calling the constructor. No model partition is rewritten.

The initializer now records a versioned NVS marker before entering third-party
model setup and clears it after successful setup. If setup panics, the next
boot skips that initializer and leaves tap-to-talk available; a changed
initializer version can retry. This guards initialization failures, not every
possible runtime crash. Hardware testing remains necessary after compilation.

Version 0.2.1 started successfully on hardware but did not react to the owner's
wake phrase at the original 0.90 threshold. Version 0.2.2 adds Setup → Wake
sensitivity, saved on release. Initial sensitivity is 70/100, mapping to a
0.31 threshold; the full slider maps 0..100 to 0.80..0.10. Higher sensitivity
can cause false wakes and needs testing around TV/background speech. The voice
task applies threshold changes before its next inference, avoiding concurrent
model changes from the UI. Detection stays paused inside Setup.

Idle diagnostics report aggregate frame count, maximum microphone RMS,
maximum inference duration and free internal heap every five seconds. Candidate
diagnostics report confidence/threshold only. No idle recordings, transcripts,
or raw audio are logged or uploaded. These metrics distinguish insufficient
microphone level, slow inference and overly strict thresholds during tuning.

## Follow-up conversations (0.3.0)

Firmware 0.3.0 requests `/api/v1/room-voice?continuous=1`. Updated Ghost Server
acknowledges `continuous: true` in ready events and keeps the same Gemini Live
session for follow-up questions, preserving that conversation's context. Other
clients keep the original one-turn protocol. An older server does not send the
capability flag, so new firmware safely falls back to one-turn operation.

After a reply finishes playing, the amplifier is disabled and microphone DMA
backlog is discarded before sending `{"type":"start"}` on the same connection.
The next ready event opens a visible **Anything else? Listening...** window.
Speak normally without another wake phrase. About 1.3 seconds of quiet after
detected speech submits the question; ten seconds without detected speech ends
the conversation without generating an extra reply. **End** closes the session
at any active stage; **Send now** can submit a question manually.

The microphone is not uploaded while Ghost speaks or after the conversation
ends. Audio during each displayed listening window is sent to the server and
voice provider, including silence until End/timeout. There is no acoustic
barge-in or full echo cancellation in this prototype. Quiet-speech detection
and the short playback-to-listening gap need physical testing.

Each recording remains bounded to 20 seconds on the server (19 on firmware).
Continuous sessions allow at most 12 completed turns or five minutes, with
the existing device/session concurrency limits and access rechecks. Input byte
limits and transcripts reset per turn; transcripts stay associated with the
same authenticated device. Tools/audio are accepted from the provider only
during the reply phase. Cancel/disconnect releases the provider and active slot.

Deployment order: merge the server change and update/restart the NAS container,
verify the continuous capability, then test the new firmware. Merely flashing
0.3.0 cannot enable follow-ups against an older running server. Tests cover
provider reuse, two questions in one session, transcript separation, per-turn
limits, silent cancellation, turn caps, revocation and out-of-phase tools.

Version 0.3.0 was installed on the owner's 4B and follow-up questions without
another wake phrase were confirmed working. Logs also show the quiet timeout
closing normally with WebSocket code 1000. A separate generated-speech server
test completed two turns on one connection. This does not validate every
room/noise condition or acoustic interruptions.

## Capture responsiveness (0.3.1)

The 0.3.0 board trace showed 100 ms audio writes taking 731–1415 ms at
approximately -66 to -70 dBm. Since capture and upload shared one task,
250 microphone frames (five seconds of PCM) took about nine seconds to read.
Firmware 0.3.1 moves the blocking network writes to a separate task, keeping
20 ms microphone reads and local silence detection independent of those writes.

A fixed 32-slot queue in PSRAM holds at most 3.2 seconds of 100 ms batches
(about 103 KB including metadata), not an unbounded recording. Slots remain
owned by the sender during a blocking write and are cleared before reuse.
The final partial batch and stop marker preserve order. Writes are paced at
16 kHz mono PCM; queued blocks older than three seconds or a full queue stop
the session with an error. A failed/partial write is never replayed. This
absorbs short stalls; it cannot repair sustained poor Wi-Fi throughput.

End cancels new capture/queued uploads immediately. A write already in progress
may finish; the network worker must release its slot before the voice task
destroys the client. Follow-ups cannot reuse an in-flight upload. Queued audio
is discarded on cancellation and never carried into a new conversation.

Mouth attack/release smoothing uses elapsed time rather than a fixed per-frame
factor. Unchanging microphone visibility and eye pivot styles are no longer
set every frame. Aggregate display frame intervals are logged every five
seconds, excluding hidden Setup time. No animation frame-rate increase or
wake-recognition model change is included. Native C queue tests cover concurrent
producer/consumer order, held-slot ownership, capacity, cancellation, partial
tail/stop ordering, erasure and counter rollover. Physical testing must compare
capture frame timing, follow-ups, End, Wi-Fi stalls and visual smoothness before
claiming an end-to-end speed improvement.

## Fluid character motion (0.3.2)

This animation-only firmware is based on working 0.3.0. It does not contain
the withdrawn 0.3.1 queued-uploader experiment. WebSocket handling, microphone
capture, playback, wake recognition initialization and Wi-Fi configuration
remain unchanged. During stronger-signal testing, 0.3.0 captured about 49.9
frames/sec with no slow-upload warnings; retain that baseline for comparison.

A small procedural motion model uses bounded, critically damped springs for
body sway, attentive posture, gaze, drapes and hem. Cloth offsets trail body
velocity and settle smoothly. This is lightweight 2D shape animation rather
than a cloth simulation or a new 3D renderer. No whole-avatar rotation or
scaled off-screen texture is introduced. The existing 33 ms timer remains.

Listening eases into a quieter float and centered gaze; thinking looks gently
upward; idle glances vary over time. Blinks have a short close/hold and softer
opening. Mouth motion uses elapsed-time attack/release smoothing with a slower
close and a small tongue visible while open. A narrow ground shadow changes
width with height. The taller invisible avatar container leaves room for the
moving hem, and the status label remains below it.

The motion model caps simulation steps after delayed callbacks and pauses
while Setup hides the avatar. Five-second aggregate timer diagnostics support
physical comparison without logging audio or private settings. A native C test
exercises ten minutes of pose changes, motion bounds, delayed/invalid frame
recovery, blink activity, mouth easing and 30/60 Hz path consistency. Hardware
testing must still check visual quality, frame intervals and live conversation.

Firmware 0.1.3 anchors the password keyboard to the bottom of the 480-pixel
screen; the previous position offset could place it below the visible area.
Microphone input gain is 30 dB (up from 24 dB). The level bar uses a smoothed
logarithmic scale from -50 to -18 dBFS, so ordinary speech is more visible.
The bar is feedback, not a calibrated loudness or recognition score. Check
speech recognition and clipping on the physical board at the intended distance.

Firmware 0.1.4 retains 20 ms microphone reads but sends five frames together
as one 3,200-byte message every 100 ms. This reduces small WebSocket writes
fivefold. Finish flushes the final partial batch before the stop command;
cancel discards it. Failed writes end the turn rather than replaying potentially
partially transmitted audio. Slow/failed uploads log frame count, byte count,
duration, RSSI and free internal heap, never audio or credentials. This is a
transport mitigation requiring repeated physical testing, not proof of a fix.

Server tests cover the PCM protocol, resampling across arbitrary packet cuts,
input bounds, credential redaction, disconnected sessions and concurrency.
GitHub Actions compiles the actual target using ESP-IDF 5.5.2, BSP 2.0.0, LVGL
9.5.0, LVGL port 2.8.0~1 and WebSocket client 1.8.0. The resolved dependency lock
and source commit travel with the application artifact.

A successful build is not proof of working microphones, correct channel mapping,
speaker volume, display/touch timings or echo behavior. These require the
physical board. Also check cancel, silence, server outage, lost Wi-Fi, revocation,
large replies, repeated turns and return to setup. A restore from the private
backup has not yet been tested.
