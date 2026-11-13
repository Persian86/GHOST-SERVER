# Tablet cameras and Home news

The tablet's Cameras button still opens all live feeds. Tap a camera to select it,
then pinch or use + / − to digitally zoom (up to 6×); drag to pan and Reset to fit.
This does not move physical PTZ cameras. Save photo keeps the full camera image.

Record video / Stop recording saves an H.264 MP4 on Ghost Server, without audio.
Clips stop automatically after five minutes, about 100 MiB, or link expiry,
whichever comes first. Two recordings can run at once (one per viewer link).
FFmpeg is already included in the server Docker image; other installations need
an FFmpeg build with libx264 on PATH. A recording opens an additional camera
connection, so camera/NVR connection limits still apply.

Saved opens photos and videos inline and allows deletion (tap Delete twice).
Files live under `$GHOST_DATA/camera-media` (normally `/data/camera-media` on the
NAS), separate from shared documents. There is a 1 GiB storage cap, with reserved
space for running clips; remove saved items when full. Existing files are never
automatically deleted. Incomplete crash recordings are removed at server startup.
Back up this directory with the server data volume.

Media is available only through a live camera-view link scoped to those cameras.
Links are bearer credentials: do not share them. Saved files remain after a link
expires; reopen Cameras to get a fresh link and view them again. Closing the
viewer requests a stop; automatic limits still apply if that request cannot arrive.

## GNews

Open Ghost Server admin → Brain & keys → Household → GNews API key (server only).
Enter your key and save settings. It is masked in admin responses and is never
included in dashboard data or the tablet. It is stored with the server's other
credentials in its settings database; protect backups accordingly.

GNews requests top general headlines in English for News country (Australia by
default), with a 30-minute dashboard cache. Missing keys, exhausted quotas or
provider errors fall back to the existing news feed. Headline cards carry the
publisher, publication time when supplied, and a link to the original article.
No key has been provisioned by this update; configure your own on the server.

The matching tablet update gives news its own right column and removes duplicated
Home device controls, camera previews and the large Ghost strip. Rooms/Cameras
navigation and the full Ghost conversation/gestures page remain available.

## Validation

Run `python -m pytest tests -q` and `node tests/check_camera_viewer.cjs`.
The encoder test uses a generated local test clip, never household cameras.
An FFmpeg installation enables that test; it skips explicitly otherwise.
Real HA/RTSP camera compatibility, NAS capacity and tablet WebView interaction
must also be checked on the household hardware after installation.
