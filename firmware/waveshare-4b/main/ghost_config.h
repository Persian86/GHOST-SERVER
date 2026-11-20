#pragma once
#include <stdbool.h>
typedef void (*ghost_status_fn)(const char *message);
typedef struct {
    char ssid[33];
    char password[65];
    char server[96];
    char key[160];
} ghost_config_t;
bool ghost_config_connect(ghost_config_t *config, ghost_status_fn status);
bool ghost_wifi_ready(void);
#include <stddef.h>
#include "esp_err.h"
// The paired Ghost Server address (e.g. "http://192.168.80.165:8780").
void ghost_config_server(char *out, size_t capacity);
// Re-pairs with a (possibly different) Ghost Server from the touchscreen, keeping
// Wi-Fi. `server` may be typed loosely ("192.168.80.165"); `code` is the six-digit
// code from the admin page. The old pairing stays until the new one is saved.
// ESP_OK, ESP_ERR_INVALID_ARG (address), ESP_ERR_INVALID_SIZE (code format),
// ESP_ERR_WIFI_NOT_CONNECT, ESP_ERR_TIMEOUT (server unreachable),
// ESP_ERR_NOT_FOUND (wrong/expired code), ESP_ERR_INVALID_STATE (busy or paused).
esp_err_t ghost_server_repair(const char *server, const char *code);

// HTTPS. Ghost Server makes its own certificate; the screen pins it (the exact
// certificate, not a name) when pairing, or once on its first start after this
// update. From then on voice and pairing use HTTPS/WSS with only that
// certificate accepted, and never fall back to plain HTTP. Re-pairing fetches it again.
#define GHOST_PIN_MAX 1400
// Copies the pinned certificate and HTTPS port; false while still plain HTTP.
bool ghost_server_pin(char *pem, size_t capacity, int *port);
// First 16 hex digits of the pinned certificate's SHA-256, spaced like the
// admin page ("CBEC EE0F 32D5 7B7A"); "" while plain HTTP.
void ghost_server_fingerprint(char *out, size_t capacity);
// Background task that pins the certificate of an already-paired server.
void ghost_server_secure_start(void);
#include "esp_err.h"
#include <stddef.h>
typedef struct {char ssid[33]; int rssi; bool secured;} ghost_network_t;
esp_err_t ghost_wifi_scan(ghost_network_t *networks, size_t capacity, size_t *count);
esp_err_t ghost_wifi_update(const char *ssid, const char *password, bool keep_password);
bool ghost_wifi_status(char *ssid, size_t capacity, int *rssi);
