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
