# 板子"未连接 WiFi"误判 — 排查报告

日期：2026-11-19 14:10
板子：ESP32-S3-Touch-LCD-1.85B（MAC `44:bd:8d:78:14:68`）
固件：`yuanma/AI-Pet-Badge-main`（device_id = `ai-pet-badge`）

## 结论先说

**你的判断是对的：板子 WiFi 一直连着，没连上的是"天气"。**

屏幕左下角那张天气卡片显示 `--°` + 「更新中」，容易被当成"没联网"。
实际上板子的 WiFi、DHCP、WebSocket 语音链路全部正常，
唯一失败的是天气模块 —— 它卡在 **DNS 解析** 这一步。

## 证据链

### 1. 板子 WiFi 是通的（串口实测）

```
I (2785) wifi:connected with Isla2Badge, aid = 1, channel 1, 40U, bssid = 96:de:80:c9:81:b5
I (2786) wifi:security: WPA2-PSK, phy: bgn, rssi: -49
I (3810) esp_netif_handlers: sta ip: 192.168.137.254, mask: 255.255.255.0, gw: 192.168.137.1
I (3810) badge_net: Wi-Fi connected  IP=192.168.137.254  GW=192.168.137.1
```

`BADGE_DASHBOARD` 回报：

```json
{"wifi":true,"time_valid":true,"time":"2026-11-19 14:09:xx",
 "rtc":true,"city":"Shenzhen","weather_valid":false,
 "temperature":0.0,"weather_code":0,"observed":0}
```

- `wifi: true` → WiFi 已连
- `time_valid: true` → NTP 校时成功（说明**能访问外网**，见下方关键推论）
- `weather_valid: false` → 只有天气失败

### 2. WebSocket 语音链路也是通的

`http://127.0.0.1:8765/api/devices`：

```json
{"device_id":"ai-pet-badge","peer":"('192.168.137.254', 64769)",
 "hello_done":true,"connected_s":260.9,"state":"idle",
 "last_heartbeat_s":6.0,"heartbeat_ok":true}
```

心跳 6 秒前刚收到，`heartbeat_ok: true`。

### 3. 天气失败的真实报错（串口抓到的）

```
E (1343312) esp-tls: couldn't get hostname for :api.open-meteo.com:
                       getaddrinfo() returns 202, addrinfo=0x0
E (1343313) esp-tls: Failed to open new connection
E (1343313) HTTP_CLIENT: Connection failed, sock < 0
W (1343313) badge_net: Weather fetch failed: ESP_ERR_HTTP_CONNECT, HTTP 0
```

`getaddrinfo() returns 202` = `ESP_ERR_ESP_TLS_CANNOT_RESOLVE_HOSTNAME`（DNS 解析失败）。
注意它是 **DNS 失败**，不是 TCP/HTTPS 失败。

### 4. 根因：Windows ICS 的 DNS 代理（192.168.137.1:53）坏了

板子的 DNS 由 DHCP 下发，指向热点网关 `192.168.137.1`。
实测这个 DNS 代理不应答：

| 查询目标 | 结果 |
|---|---|
| `192.168.137.1:53`（板子的 DNS） | **ConnectionReset / TIMEOUT**（3 次全部失败） |
| `10.20.5.1`（PC 有线 DNS） | OK rcode=0 answers=1 |
| `223.5.5.5`（阿里 DNS） | OK rcode=0 answers=1 |
| `8.8.8.8`（Google DNS） | OK rcode=0 answers=1 |

`netsh` 也确认：

```
> nslookup api.open-meteo.com 192.168.137.1
*** UnKnown can't find api.open-meteo.com: No response from server
```

**53 端口的归属**：`0.0.0.0:53` 被 PID 4068 独占，而 4068 正是
`SharedAccess`（Internet Connection Sharing）服务。也就是说
ICS 的 DNS 代理进程在跑、端口在监听，但对查询直接 RST —— 典型的
"服务状态坏掉但进程没退出"。

### 5. 为什么 NTP 能成功、天气不能？

两者都走外网，但：

- **NTP**（`ntp.aliyun.com`）— 固件里 SNTP 走的是 lwIP 自带解析，
  且实测 `time_valid: true`，说明 NTP 这条路当时通了。
- **天气** — 走 `esp_http_client` + `esp_crt_bundle`，
  依赖 `getaddrinfo()` → 命中坏掉的 ICS DNS → 直接失败。

（补充：NTP 成功也可能是 RTC 掉电前就存了有效时间。
`BADGE_STATUS` 里 `"rtc":true`，`badge_dashboard_start()` 会先从 RTC 恢复时间，
所以 `time_valid:true` **不能**单独证明外网通。这点很关键，避免误判。）

## 屏幕为什么让人误会

`badge_dashboard.c` 第 155-157 行的降级文案：

```c
} else {
    text_if_changed(weather_label,
        connected ? (badge_dashboard_time_valid() ? "更新中" : "校时中") : "联网中");
}
```

截图实测显示的是 **`--°` + 「更新中」**，不是「联网中」。
「更新中」的语义是"正在拉天气"，但用户看到 `--°` 空温度，
第一反应就是"没网"。

**这里确实是个 UX 缺陷**：天气持续失败时，界面一直停在
「更新中」，永远不给"失败了"的明确提示，也没有区分
"WiFi 没连"和"WiFi 连了但天气拉不到"。

## 修复建议

### A. 立即可用（不用改固件）

1. **重启 ICS 服务**（管理员 PowerShell）：
   ```powershell
   Restart-Service SharedAccess -Force
   ```
   或直接关掉再打开"移动热点"开关。
   这是最可能一步到位的修复。

2. 验证修复：
   ```powershell
   nslookup api.open-meteo.com 192.168.137.1
   ```
   能返回 IP 就说明 DNS 代理恢复了。

3. 板子会在 60 秒后自动重试天气
   （`badge_network.c:146`，失败重试间隔 60 秒），
   不用重启板子。

### B. 固件侧加固（建议做，防止再踩）

1. **显式设置 DNS**，不依赖 ICS 下发：
   在 `badge_network_start()` 拿到 IP 后，
   用 `esp_netif_set_dns_info()` 把 DNS 设成 `223.5.5.5` / `8.8.8.8`。
   这样即使 ICS DNS 代理坏了，天气也能出。

2. **界面区分三种状态**（改 `badge_dashboard.c`）：
   - WiFi 未连 → 「未联网」
   - WiFi 已连、天气从未成功 → 「天气待同步」（配灰色）
   - 天气曾成功但超过 1 小时 → 「待更新」
   - 天气正常 → 显示温度

   现在 `weather_valid` 只区分 true/false，
   没有"连续失败 N 次"的计数，界面无从判断该说什么。

3. **加失败计数**，连续失败时打印更明确的一行日志，
   方便下次一眼定位。

## 附：本次排查用到的临时脚本

| 文件 | 作用 |
|---|---|
| `_board_probe.py` | 读串口 + 发 DASH/STATUS 命令 |
| `_boot_capture.py` | 复位板子抓启动日志 |
| `_shot_raw.py` | 抓屏幕快照原始数据 |
| `_dns_probe.py` / `_dns_probe2.py` / `_final_diag.py` | 裸 UDP 测 DNS 与 NAT |

均在 `D:\xiangmudaima\` 下，可随时删除。
