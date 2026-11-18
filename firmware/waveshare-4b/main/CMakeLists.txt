idf_component_register(SRCS "ghost_main.c" "ghost_config.c" "ghost_wifi_ui.c" "ghost_wake.c" INCLUDE_DIRS "."
    REQUIRES esp_wifi esp_event esp_netif nvs_flash esp_http_server esp_http_client
    esp_timer json esp_psram app_update)

# Pack only the English model into the application. Never replace the owner's
# original model partition or require a new partition table.
idf_component_get_property(sr_dir espressif__esp-sr COMPONENT_DIR)
set(ghost_models "${CMAKE_CURRENT_BINARY_DIR}/ghost_models")
file(MAKE_DIRECTORY "${ghost_models}/mn7_en")
foreach(model_file _MODEL_INFO_ mn7_index mn7_data vocab)
    configure_file("${sr_dir}/model/multinet_model/mn7_en/${model_file}"
                   "${ghost_models}/mn7_en/${model_file}" COPYONLY)
endforeach()
# Keep Espressif's redistribution notice inside the embedded model bundle.
configure_file("${sr_dir}/LICENSE" "${ghost_models}/mn7_en/LICENSE" COPYONLY)
execute_process(COMMAND "${PYTHON}" "${sr_dir}/model/pack_model.py"
    -m "${ghost_models}" -o "${CMAKE_CURRENT_BINARY_DIR}/ghost_wake.bin"
    COMMAND_ERROR_IS_FATAL ANY)
target_add_binary_data(${COMPONENT_LIB} "${CMAKE_CURRENT_BINARY_DIR}/ghost_wake.bin" BINARY)
