# Direct Reolink video in Ghost

Ghost can negotiate receive-only WebRTC video through a private go2rtc relay. Camera passwords and RTSP addresses remain on Ghost Server. The tablet receives SDP connection information and video, with no Reolink account credentials. No APK change is needed.

## Install the relay on the Ghost Server NAS

Copy `go2rtc.yaml` and `docker-compose.camera.yml` alongside the existing server compose file. Start the optional relay:

```sh
docker compose -f docker-compose.yml -f docker-compose.camera.yml up -d
```

Both services use host networking. The relay API binds only to `127.0.0.1:1984`; do not expose this API or forward it on your router. Media uses TCP/UDP port 8555 on the NAS and must be reachable from the tablet on your home network. No public STUN service or internet port forwarding is needed. Check that another camera service is not already using 8555 before starting this relay. A different media port can be selected in `go2rtc.yaml`.

For a NAS stack editor, mount the actual absolute path of `go2rtc.yaml` at `/config/go2rtc.yaml` read-only, or set `GHOST_GO2RTC_CONFIG` to that absolute path. The relay is pinned to version 1.9.15. Camera sources are registered in memory, so the configuration can remain read-only.

## Connect a Reolink Home Hub

Open Ghost Server admin → Integrations → Security cameras. Enable **Direct Reolink / WebRTC playback**. Keep the private relay address `http://127.0.0.1:1984` when using the supplied deployment.

In **Direct preview streams**, enter each existing camera's exact name or Home Assistant entity ID, followed by its hub stream address. For example (replace all placeholders):

```text
Front door = rtsp://HUB_USER:HUB_PASSWORD@HUB_IP:554/Preview_03_sub
```

Use the **Home Hub's local username/password**, rather than Reolink cloud-account credentials. Percent-encode special characters in the username/password when writing a URL. Use H.264 for browser/tablet compatibility. Preview substreams are lighter for a camera grid.

Home Hub RTSP channel numbers start at 1, while `GetChannelstatus` API results start at 0: API channel 2 corresponds to `Preview_03_sub`. Confirm the channel mapping in the hub's local web interface; do not assume the order of camera tiles in the app is the RTSP number. See [Reolink's stream/channel documentation](https://support.reolink.com/articles/900000630706-Introduction-to-RTSP/).

Optionally enter matching names in **Enlarged streams**, using the corresponding `_main` address. Enlarging switches to that source; returning to the grid restores the preview source. Leave this blank if the main stream is H.265-only or unreliable; Ghost will continue using the compatible preview video. The movie player's fixed 1080p policy does not change camera encoding settings.

Save, then reopen Cameras on the tablet. A matching Home Assistant camera keeps its original snapshot/recording identity. A new name adds a direct camera. Direct playback is labelled **Direct live**. Failed connections or video that stops advancing fall back to the existing preview, labelled **Preview fallback**; this does not imply an end-to-end latency measurement. Changing pages, opening Saved, or expiring the viewing page closes its active video peers.

## Validation and limitations

Automated checks cover scoped view links, expired links, receive-only offers, bounded request bodies, masked settings, source selection, private signalling errors, quality switching, stale responses, startup timeout and peer cleanup. The existing zoom, capture, record and gallery controls remain tested.

The optional real-browser smoke check is `tests/rtc_smoke.py`: provide `GO2RTC`, `FFMPEG`, `NODE` and `CHROME` executable paths and install `playwright-core` locally. It uses generated video, a private loopback signalling API and a temporary local-network media port, not household camera feeds.

Battery-camera wake-up time, Home Hub firmware, codec compatibility, Wi-Fi and NAS load can still affect latency. Actual household feeds need verification after the relay and hub credentials are configured. This does not embed the Reolink app, provide two-way camera audio, or change any camera settings.
