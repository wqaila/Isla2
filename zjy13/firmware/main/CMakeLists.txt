idf_component_register(
    SRCS
        "main.c"
        "app_protocol.c"
        "app_net.c"
        "app_audio.c"
        "app_ui.c"
        "app_state.c"
    INCLUDE_DIRS
        "."
    REQUIRES
        esp_wifi
        esp_netif
        esp_event
        esp_websocket_client
        nvs_flash
        driver
        esp_lcd
        esp_psram
        esp_app_format
        json
        esp_driver_usb_serial_jtag
)

# 依赖 PSRAM（帧缓冲 + 音频缓冲都在 PSRAM）
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    CONFIG_APP_UI_LCD_ENABLE=0
)
