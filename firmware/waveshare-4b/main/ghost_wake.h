#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

bool ghost_wake_init(void);
bool ghost_wake_ready(void);
bool ghost_wake_enabled(void);
esp_err_t ghost_wake_set_enabled(bool enabled);
int ghost_wake_sensitivity(void);
esp_err_t ghost_wake_set_sensitivity(int sensitivity);
// Only the voice task calls reset/feed; idle samples never leave this device.
void ghost_wake_reset(void);
bool ghost_wake_feed(const int16_t *samples,size_t count);
