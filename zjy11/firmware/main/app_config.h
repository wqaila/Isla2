#pragma once

/*
 * 本机配置模板：占位值，真实值在编译期通过 Kconfig 或
 * idf.py menuconfig 填写，不要把真实凭据提交进仓库。
 *
 * 只允许保存「设备令牌」。绝不要在这里写模型服务商的 API Key：
 * 模型密钥只存在于电脑网关。
 */

/* 电脑网关地址：必须是局域网 IP，不能用 127.0.0.1（那指向板子自己）。 */
#define APP_GATEWAY_BASE_URL   CONFIG_ZJY11_GATEWAY_URL

/* 设备令牌：由网关生成，可用 GET 网关 /health 之外的方式在本机读取。 */
#define APP_GATEWAY_DEVICE_TOKEN CONFIG_ZJY11_DEVICE_TOKEN

/* Wi-Fi：第一版只支持 2.4 GHz。 */
#define APP_WIFI_SSID          CONFIG_ZJY11_WIFI_SSID
#define APP_WIFI_PASSWORD      CONFIG_ZJY11_WIFI_PASSWORD
