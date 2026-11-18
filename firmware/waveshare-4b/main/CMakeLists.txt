idf_component_register(SRCS "ghost_main.c" "ghost_config.c" INCLUDE_DIRS "."
    REQUIRES esp_wifi esp_event esp_netif nvs_flash esp_http_server esp_http_client
    esp_timer json esp_psram)
