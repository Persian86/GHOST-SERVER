#pragma once
#include <stdbool.h>
#include "esp_err.h"

bool ghost_audio_ready(void);
int ghost_audio_volume(void);
// Called from Setup while no voice turn is active. Applies and saves 0..100.
esp_err_t ghost_audio_set_volume(int volume);
