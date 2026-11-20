#pragma once
#include <stdbool.h>

// Pure checks shared by the setup hotspot page and the on-screen Server & pairing
// page. No ESP-IDF dependencies, so tests compile them natively.

// Only plain HTTP on a private LAN IPv4 address (10/8, 172.16/12, 192.168/16),
// optional port, nothing else: no names, paths, userinfo or public addresses.
// Credentials and the device key are only ever sent to such an address.
bool ghost_server_url_ok(const char *url);

// What someone types on the touchscreen -> the canonical form above:
// "192.168.80.165" -> "http://192.168.80.165:8780"; surrounding spaces and a
// trailing "/" are dropped. Returns false if the result isn't acceptable.
#include <stddef.h>
bool ghost_server_url_normalise(const char *typed, char *out, size_t capacity);

// Exactly six ASCII digits.
bool ghost_pair_code_ok(const char *code);

// Auto-hiding buttons. They stay up while Ghost is doing anything (busy: not
// plain idle) and for hide_ms after the last touch or the end of the last
// conversation; then they fade away until the screen is touched again.
#include <stdint.h>
#define GHOST_CONTROLS_HIDE_MS 8000
bool ghost_controls_visible(bool busy, int64_t ms_since_touch, int64_t ms_since_busy, int64_t hide_ms);

// Voiced 20 ms frames needed before the listener treats sound as speech.
#define GHOST_SPEECH_MIN_FRAMES 8

// Night mode: after this long idle with no touch and no conversation the
// screen dims and Ghost closes its eyes. The wake word keeps listening.
#define GHOST_SLEEP_MS 600000
bool ghost_sleepy(bool idle, int64_t ms_since_touch, int64_t ms_since_busy);

// The wake recognizer's copy of the microphone: a fixed 2x boost (6 dB),
// clipped. At 1 m "Hey Ghost" reached the recognizer at only ~-29 dBFS.
int16_t ghost_wake_gain(int32_t sample);

// Card text for the screen's fonts (plain ASCII plus the degree sign): copies
// src into dst, turning "…" into "..." and any other non-ASCII character or
// control character into a single space; always terminates dst.
void ghost_card_text(char *dst, size_t capacity, const char *src);

// How long a card stays up before Ghost's face comes back.
#define GHOST_CARD_MS 30000

// HTTPS: the screen keeps its paired address as "http://IP:8780" and, once it
// has pinned the server's certificate, talks to "https://IP:<tls port>".
// Builds "https://IP:port<path>" from that address; false if it doesn't fit.
bool ghost_https_url(const char *server, int port, const char *path, char *out, size_t capacity);

// A single PEM certificate small enough to keep: BEGIN/END lines around
// base64 only, and shorter than capacity (including the terminator).
bool ghost_pem_ok(const char *pem, size_t capacity);
