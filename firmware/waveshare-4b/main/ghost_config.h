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
#include "esp_err.h"
#include <stddef.h>
typedef struct {char ssid[33]; int rssi; bool secured;} ghost_network_t;
esp_err_t ghost_wifi_scan(ghost_network_t *networks, size_t capacity, size_t *count);
esp_err_t ghost_wifi_update(const char *ssid, const char *password, bool keep_password);
bool ghost_wifi_status(char *ssid, size_t capacity, int *rssi);
