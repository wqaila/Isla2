# 第一版组件：所有业务代码在 main/ 下。
# LVGL 由 BSP 组件间接引入；显示/触摸驱动来自官方 BSP。
idf_component_register(
    SRCS
        "main.c"
        "app_text.c"
        "app_json.c"
        "app_protocol.c"
        "app_net.c"
        "app_ui.c"
    INCLUDE_DIRS "."
    REQUIRES
        esp_wifi
        esp_netif
        esp_event
        esp_http_client
        esp_driver_usb_serial_jtag
        lvgl__lvgl
        waveshare__esp32_s3_touch_lcd_1_85B
    PRIV_REQUIRES
        mbedtls
        esp_timer
)
