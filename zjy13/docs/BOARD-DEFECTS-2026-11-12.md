# 板子与固件缺陷排查（2026-11-12）

> 排查方式：读真实串口日志 + 静态代码审查 + 服务端对照
> 当前状态：固件已烧录（8.7MB / 9MB 分区，余 6%），服务端在跑，
> 板子已连上 WiFi 与服务端（`BADGE_VOICE_PING {"conn":1}`）
> 结论：**能演示，但有 1 个高频致命缺陷 + 6 个功能性缺口 + 若干硬件天花板**
>
> **2026-11-12 更新**：P0「长按说话录不到音」**已修复并烧录**（方案 A + B 都做了），
> 详见第七节。其余缺口仍未动。

---

## 一、致命缺陷：长按说话很容易录不到声音 ✅ 已修复

### 现象（串口实录）

```
I (12118) badge: 长按说话 -> start=0
I (12272) voice: 停止录音
I (12272) badge: 松开发送
E (12310) voice: 服务端错误 asr_failed: 识别失败:
          音频过短（0.080s < 0.2s），拒绝识别
```

从「开始录音」到「松开发送」只隔 **154 毫秒**，实际录到 **0.08 秒**，
服务端直接拒绝识别。用户会看到「说了话但没反应」。

### 根因

LVGL 的事件模型与「按住说话」的直觉不匹配：

| 事件 | 触发时机 | 本意 |
|---|---|---|
| `LV_EVENT_LONG_PRESSED` | **按住满 300ms 时触发一次** | 开始录音 |
| `LV_EVENT_RELEASED` | **松手时触发** | 停止录音 |

问题在于：录音**不是在按下时开始，而是在按满 300ms 时才开**。
所以用户实际可用的话音窗口 = 按住总时长 − 300ms。

按 500ms → 只有 200ms 可录；按 400ms → 只有 100ms。
而录音块是 **40ms** 一块（`CHUNK_MS=40`），
且录音线程从收到请求到真正 `esp_codec_dev_read` 还有开销，
所以按 400ms 以内基本拿不到有效音频。

### 雪上加霜：没有最短时长保护

固件侧**完全没有**「录音太短就别发」的判断，直接 `recording_stop`，
把 0.08 秒的音频发上去，由服务端报错。用户看到的是错误弹窗而不是
「按住说话时间太短」这类可理解的提示。

### 修复建议（按推荐度）

**方案 A（推荐，改动小）：改按下即开始录音**

```c
lv_obj_add_event_cb(screen, on_voice_press,   LV_EVENT_PRESSED,  NULL);  // 按下就开始
lv_obj_add_event_cb(screen, on_voice_release, LV_EVENT_RELEASED, NULL);  // 松手停止
```

但要保住「短按切动画」，需要加一个判定：
按下时先开录音，若 300ms 内就松开则**取消本轮录音**并按短按处理。

**方案 B（最简单，零风险）：固件加最短时长保护**

松手时若录音时长 < 300ms，不发 `recording_stop`，改为直接本地取消
并提示「按住说话久一点」。用户至少能得到明确反馈，不再看到服务端错误。

**方案 C：两者都做** —— A 解决体验，B 兜底异常。

> **实际采用：方案 C（A + B 都做了）**，见第七节。

---

## 二、功能性缺口（6 项）

### 1. 不支持打断（播放中无法插话）

服务端 `_on_cancel` 完整可用，但**固件从不主动发 `cancel`**：

```
voice_client.c 只处理收到的 turn_cancelled（line 644）
没有发送 "cancel" 的代码
```

后果：爱莉希雅说长句时（实测可达 10 秒），用户只能等它说完。
这是语音交互里最影响体验的缺失之一。

**修法**：播放中检测到长按 → 发 `{"type":"cancel","turn_id":"..."}`
→ 清空本地播放缓冲 → 回 IDLE。服务端已支持，只需固件配合。

### 2. 没有唤醒词

当前必须长按屏幕才能说话。`FAULT` 与 `board-firmware-audit` 都记录过
板子 BSP 支持 4 通道 TDM 与硬件唤醒词能力（`wake_word_drv.c`），
但**本项目未接入**。

**代价**：演示时每次都要腾出手按屏幕，不够「智能」。

### 3. 不支持 OTA

分区表有 `otadata`，但**没有 `ota_0`/`ota_1`**：

```
nvs,      data, nvs,     0x9000,    0x4000
otadata,  data, ota,     0xd000,    0x2000
phy_init, data, phy,     0xf000,    0x1000
factory,  app,  factory, 0x20000,  0x900000
model,    data, spiffs,          , 5168K
```

只有单个 `factory` 分区，**改任何代码都要 USB 重烧**。
而固件已 8.5MB / 9MB，**余量只剩 0.5MB**，后续加功能会很快撞墙。

**修法**：改分区表为双 OTA 分区（各 4.5MB 左右），
但 9MB 放不下「动画 6.9MB + 双份固件」，需要先把动画素材压缩或移到 SPIFFS。

### 4. 明文 `ws://`，无鉴权

- 无 TLS
- `device_hello` 的 `token` 字段服务端支持校验（`--token`），但**默认不启用**
- 同一热点下任何设备都能连上并调用 LLM（会消耗你的 API 额度）

在自家热点内风险可接受，但**不能用于任何公开场合**。

### 5. 断连后 WiFi 重连较慢

串口实测：

```
W (929462) badge_net: Wi-Fi disconnected, reason 201
W (931869) badge_net: Wi-Fi disconnected, reason 201
W (934277) badge_net: Wi-Fi disconnected, reason 201
I (934339) wifi:state: auth -> assoc
E (935645) wifi:sta is connecting, return error
I (935908) wifi:connected with Isla2Badge
```

`reason 201`（NO_AP_FOUND）连续出现 3 次、间隔约 2.4 秒，
然后才连上。重连策略是固定间隔重试，没有指数退避，
且 `sta is connecting, return error` 说明重连调用有竞态。

### 6. 无并发多设备验证

服务端 `max_connections=4` 已实现，但**只测过单连接**。
两台板子同时说话时 LLM/TTS 会不会互相拖慢，未验证。

---

## 三、硬件天花板（软件无法解决）

| 项 | 实测/记录 | 影响 |
|---|---|---|
| **扬声器低频** | 约 300Hz 以下基本缺失 | 听感偏薄，人声厚度不足 |
| **扬声器音量** | 默认已调 85；实测 100 会破音 | 上限受限，不能长时间大音量 |
| **麦克风信噪比** | 串口日志里 ASR 大量重复字/幻觉 | 远场识别质量差，需靠近说 |
| **4 通道 TDM** | 当前按 2ch 双麦使用 | 未用满硬件能力，回声消除无输入 |
| **电池** | 未验收容量/充放电 | 只能插电演示 |

### 关于 ASR 识别质量

历史日志（`logs/ws_demo.log`）里大量这类结果：

```
文本='二零一七年十月十十四年五月月十日'
文本='没有没有没有有有有没有没有没有'
```

**注意区分两个原因**：
1. 之前 2ch 降混只用了 1 路麦克风（**已修**，`voice_channel_indices`）
2. 麦克风本身信噪比有限（**硬件天花板**）

修复后尚未在真实硬件上复测识别率，所以**改善幅度未知**。

---

## 四、演示前必须注意

| 项 | 状态 |
|---|---|
| **LLM 需要网络** | Clash 没开时会自动切直连（已实现回退），但**首轮会多一次失败往返** |
| **TTS 首次启动慢** | 需加载模型 9~11 秒 + 预热 4 秒；`wetext` 已改本地，不再联网 |
| **流式播放需新固件** | 服务端 `frames=0` 依赖固件支持结束标记帧；**旧固件会卡住不报 playback_done** |
| **固件余量仅 0.5MB** | 再加功能需先处理分区 |

---

## 五、按优先级的修复建议

| 优先级 | 项 | 工作量 | 收益 |
|---|---|---|---|
| **P0** | 长按说话录不到音（方案 B 兜底） | 20 行 | 消除最高频的失败 |
| **P0** | 长按说话录不到音（方案 A 根治） | 40 行 + 状态机 | 体验质变 |
| **P1** | 播放中打断（固件发 cancel） | 60 行 | 语音交互必备 |
| **P1** | 上板复测 2ch 降混的识别率改善 | 无代码 | 确认之前修复是否有效 |
| **P2** | 启用 `--token` 鉴权 | 5 行（服务端）+ 固件填 token | 防蹭用 API |
| **P2** | WiFi 重连加指数退避 | 30 行 | 减少无效重试 |
| **P3** | 唤醒词 | 大 | 免手按，但工作量与风险都高 |
| **P3** | OTA + 分区调整 | 中 | 免 USB，但要先解决素材体积 |

---

## 六、结论

**板子能演示，核心链路（录音→ASR→LLM→TTS→播放）是通的**，
且今天已把首块延迟从 5.1s 降到 1.7s、音量提到 85。

但**「长按说话容易录不到音」是当前最该修的问题**——
它不报错在界面上，只在串口留下 `asr_failed: 音频过短`，
用户体感就是「有时候说话没反应」，且复现概率取决于按住时长。

建议下一步优先做 P0 两项，再考虑打断与鉴权。

---

## 七、P0 修复记录（2026-11-12 当天完成）

### 改了什么

**方案 A：按下即录音（`main.c`）**

| 项 | 改前 | 改后 |
|---|---|---|
| 录音起点 | `LV_EVENT_LONG_PRESSED`（按住 300ms 后） | `LV_EVENT_PRESSED`（按下瞬间） |
| 短按处理 | `LV_EVENT_CLICKED` 单独回调 | 在 `RELEASED` 里按按住时长判定 |
| 长按阈值 | `lv_indev_set_long_press_time(300)` | 移除（不再需要） |

新增 `SHORT_PRESS_MS = 300` 与 `s_press_tick`：

```c
static void on_voice_press(lv_event_t *event) {
    s_press_tick = xTaskGetTickCount();
    voice_talk_start();            // 按下就开始录
}

static void on_voice_release(lv_event_t *event) {
    uint32_t held = (xTaskGetTickCount() - s_press_tick) * portTICK_PERIOD_MS;
    if (held < SHORT_PRESS_MS) {
        voice_talk_stop();         // 取消录音
        request_next_action();     // 并切动画
        return;
    }
    voice_talk_stop();             // 正常发送
}
```

**关键**：同时**移除了 `LV_EVENT_CLICKED`** 的注册。否则短按会在
`RELEASED` 之后再触发一次 `CLICKED`，导致动画连跳两帧。

**方案 B：最短时长保护（`voice_client.c`）**

新增 `MIN_TALK_MS = 350`（服务端下限 200ms + 150ms 余量）：

```c
if (s_record_start_us != 0 && elapsed_ms < MIN_TALK_MS) {
    ESP_LOGW(TAG, "录音仅 %ums（< %dms），已本地取消本轮。按住说话请久一点", ...);
    send_text("{\"type\":\"cancel\",...}");   // 不是 recording_stop
    s_state = ST_IDLE;
    return ESP_ERR_INVALID_SIZE;
}
```

时间戳 `s_record_start_us` 记在 `record_loop` 里（而非 `voice_talk_start`），
因为后者只是发出 `recording_start`，还要等 `session_start` 回来才真正开读，
两者差几十毫秒，用错会让时长判定偏乐观。

### 验证

**端到端（模拟固件行为，真实 TTS）**：

```
[路径 A] 短按（录音 0.1s → 固件应发 cancel）
  ✓ 收到 turn_cancelled (reason=client_cancel)

[路径 B] 正常按住 1.5s → 应走完 ASR→LLM→TTS
  playback_start @ 1580ms  frames=0
  ✓ 结束标记收到，音频 101760 字节 (2.12s)
  ✓ turn_done，链路闭环

路径 A（短按取消）: PASS
路径 B（正常说话）: PASS
```

对比修复前：短按会收到 `asr_failed: 音频过短（0.080s < 0.2s）`，
现在是干净的 `turn_cancelled`。

**编译与烧录**：

```
ai_pet_badge.bin binary size 0x87eeb0 bytes (8.7 MB)，分区余 6%
Project build complete.
Wrote 8908464 bytes at 0x00020000 in 53.7 seconds
Hash of data verified.
```

**测试**：`.venv` **432** / `.venv-asr` **442** passed，
新增 `tests/test_voice_touch_fix.py`（12 个用例）。

### 写测试时踩到的坑（记录一下）

用 Python 检查 C 源码时有两个陷阱：

1. **C 注释里的 `{` `}` 会破坏大括号配对** —— 按配对切函数体会切错
2. **C 字符串字面量是转义形式** —— 源码里是 `\"cancel\"` 而不是 `"cancel"`，
   直接按后者查找会 `ValueError`

最终改为「全文范围内检查 `\"cancel\"` 出现在 `recording_stop` 之前，
且两者之间有 `return`」，简单可靠。

### 仍需你上板确认

我验证的是「服务端收到的行为正确」，但**真实手感要你按一下**：

| 项 | 期望 |
|---|---|
| 短按（<300ms） | 切动画，不录音，串口出现 `短按 xxxms -> 取消录音，切动画` |
| 按住 1 秒 | 正常说话并听到回复，串口出现 `松开发送（按住 xxxxms）` |
| 按住 1.5 秒以上 | 同上，识别更完整 |
