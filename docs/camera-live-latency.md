# Camera wall latency

The viewer now uses one streamed binary response for all tiles. This avoids one continuous HTTP connection per image, which can exhaust a browser's per-origin connection slots. Camera inputs are read independently; each camera has one pending frame, replaced by its newest JPEG. The browser also decodes at most one frame per tile and replaces its pending frame instead of queuing old images.

The view token and expiry still authorize access. URLs, Home Assistant tokens and upstream errors stay on the server. Leaving the viewer cancels the connection and upstream tasks. Unavailable inputs reconnect independently and tiles show Connecting/Reconnecting rather than silently implying freshness. Existing snapshot, recording, gallery, zoom and pan controls are unchanged. Direct RTSP previews use low-delay FFmpeg settings and flush output promptly; recording files are unaffected.

Deploy the Ghost Server update and reopen Cameras on the tablet; no APK update is needed. Tests simulate seven cameras, fragmented packets, a slow consumer, failed feeds, link expiry and cancellation. They cannot measure your physical cameras' latency. Delay already present in Home Assistant or the camera/NVR remains upstream; if that persists, use the camera's low-latency substream configured on the server. No camera settings are changed automatically.
