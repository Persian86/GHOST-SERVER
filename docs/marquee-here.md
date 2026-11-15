# Exact tablet playback

The `marquee` tool accepts `target: "here"`. Explicit suffixes “here”, “on this tablet” and “on this screen” also select this path. Only a paired, permitted tablet with an active foreground voice request can supply the signed-in browser identity through its `marquee_player` capability. The request token is generated per live session/tool call; it is correlation metadata, not a credential.

The server verifies Marquee protocol support, prepares that tablet's player, sends one exact-screen request using the browser's current SSE connection identity, then asks the same tablet to confirm its receipt. No fuzzy screen name or active-TV fallback is used. Uncertain requests are cancelled best effort and never automatically retried. The browser independently refuses to release unconfirmed requests. A successful tool response means queued, not already playing.

Update Marquee first, then this server, then the tablet APK. The usual Marquee assistant key remains in server settings. The tablet signs into the regular Marquee site; no key or session cookie is exposed through the native bridge. Existing named-TV commands and widget endpoints remain compatible.

Tests: `python -m pytest tests/test_marquee_here.py -q`. Complete HTTP/authentication regression checks run with the repository's normal CI suite. Physical playback and Gemini conversation completion have not been verified by these tests.
