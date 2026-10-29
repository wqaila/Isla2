set(BADGE_CHARACTER "elysia" CACHE STRING "Badge character: elysia or qinglan")
if(BADGE_CHARACTER STREQUAL "qinglan")
    set(badge_asset_file "qinglan.badge")
elseif(BADGE_CHARACTER STREQUAL "elysia")
    set(badge_asset_file "animations.badge")
else()
    message(FATAL_ERROR "Unknown BADGE_CHARACTER: ${BADGE_CHARACTER}")
endif()

idf_component_register(
    SRCS "main.c" "badge_dashboard.c" "badge_network.c"
         "badge_imu.c" "badge_imu_filter.c" "badge_motion.c" "badge_scene.c" "badge_selftest.c" "badge_storage.c"
         "badge_ui_14.c" "badge_name_20.c" "badge_clock_30.c" "badge_icons_26.c" "voice_client.c"
    INCLUDE_DIRS "."
    REQUIRES nvs_flash esp_timer esp_rom esp_driver_usb_serial_jtag fatfs sdmmc
             esp_wifi esp_event esp_netif esp_http_client esp-tls json lwip
             waveshare__esp32_s3_touch_lcd_1_85B waveshare__pcf85063a waveshare__qmi8658
    EMBED_FILES "${badge_asset_file}"
)
if(BADGE_CHARACTER STREQUAL "qinglan")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE BADGE_CHARACTER_QINGLAN=1)
endif()
