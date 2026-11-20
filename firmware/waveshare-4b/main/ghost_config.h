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
#include "esp_err.h"
#include <stddef.h>
typedef struct {char ssid[33]; int rssi; bool secured;} ghost_network_t;
esp_err_t ghost_wifi_scan(ghost_network_t *networks, size_t capacity, size_t *count);
esp_err_t ghost_wifi_update(const char *ssid, const char *password, bool keep_password);
bool ghost_wifi_status(char *ssid, size_t capacity, int *rssi);
