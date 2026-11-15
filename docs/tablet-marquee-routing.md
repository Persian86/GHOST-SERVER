# Tablet Marquee routing

The `marquee` tool accepts `action=open` to open the requesting tablet's library through its authenticated foreground voice host. The tablet also exposes a direct `open_marquee` capability, so “open Marquee” is a page action rather than a movie search or Android package-name guess. Opening the library starts no movie and still requires normal Marquee sign-in.

For tablet voice requests, `action=command` defaults to the exact embedded player. Use `target=named` with an explicitly named other TV/screen to keep remote routing. Other device kinds retain their existing route. Here-targeting still verifies the browser connection and queue receipt and never falls back to a different device. Pronouns such as “it” must resolve to a title from the conversation or prompt a clarification.

Cold player preparation has a bounded 18-second server timeout matching the tablet's longer preparation window. This does not retry playback. Existing permission checks, per-call correlation, cancellation and uncertainty handling remain in place.

Update this server, then install the companion tablet APK. The prior Marquee browser-bridge update must also be running. Tests cover default exact-tablet routing, explicit other-device routing, page opening, permission/current-call requirements and phone compatibility. Physical playback remains untested.
