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
