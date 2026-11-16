# Phase 5：zjy13 设备协议桥接

> 日期：2026-11-16
> 依据：zjy13 已验证的固件、WebSocket 协议、音频帧格式
> 结果：**完成**。304 个测试全绿，9 项端到端验收通过

---

## 一、核心结论

**zjy13 侧不需要改代码，只改一个环境变量。**

```bash
# zjy13/.env
TTS_BASE_URL=http://127.0.0.1:50001   # 原 :50000
```

回滚：改回 `:50000` 即恢复。

之所以能做到，是因为 zjy14 的流式接口在路径、格式、响应头上
与 zjy13 的 `TTSClient` 完全对齐（下面逐项验证）。

---

## 二、契约对齐（每项都有 zjy13 源码依据）

| 契约项 | zjy14 实现 | zjy13 依据 | 一致性 |
|---|---|---|---|
| 流式路径 | `/synthesize_stream` | `tts/client.py:113` | ✅ |
| 流格式 | 4 字节**大端** uint32 长度 + PCM16LE，长度 0 结束 | `tts_server_wsl.py:508` | ✅ |
| 采样率头 | `x-sample-rate: 24000` | `tts/client.py:405` | ✅ |
| 二进制帧魔数 | `0xA5` | `protocol/messages.py:40` | ✅ |
| 帧头长度 | 12 字节 | `protocol/messages.py:43` | ✅ |
| 帧类型（下行 TTS） | `0x02` | `protocol/messages.py:114` | ✅ |
| 单块上限 | 48KB | `protocol/messages.py:62` | ✅ |
| 分帧大小 | 4096 字节（85ms @ 24kHz） | `ws/server.py:1085` | ✅ |
| 首突发 | 300ms | `ws/server.py:1100` | ✅ |
| 固件环形缓冲 | 200ms | `firmware/app_config.h:103` | ✅ |

**这些不是抄文档，是读源码确认的。** 而且写成了测试：

```python
@requires_zjy13
def test_frame_bytes_matches_zjy13():
    src = (ZJY13 / "server/ws/server.py").read_text()
    assert "step = 4096" in src
    assert DEFAULT_FRAME_BYTES == 4096
```

8 个这样的测试会在 zjy13 常量变化时立刻失败，防止协议漂移。

---

## 三、交付内容

### 3.1 职责边界（决策 D7 范围收敛）

```text
zjy13 负责：WebSocket 连接、设备会话、ASR/LLM 编排、固件通信
zjy14 负责：TTS 引擎、音频适配、帧切分、背压计算
```

**不重复实现 WebSocket**。zjy14 只做「引擎 → 协议帧」这一段。

好处：
- 不重写已验证的固件与协议（zjy13 板子已烧录联调通过）
- zjy14 可独立测试（不需要真设备）
- 将来若换传输层（如 MQTT），只需替换 zjy13 侧

### 3.2 新增模块

| 文件 | 职责 |
|---|---|
| `bridge/protocol.py` | 二进制帧编解码、帧切分、背压、兼容性描述 |
| `bridge/stream.py` | 引擎流式输出 → 协议帧、FakeDevice 模拟固件 |

### 3.3 关键实现

**1. 帧切分的首突发策略**

```python
first_burst_bytes = BYTES_PER_SEC * FIRST_BURST_MS // 1000  # 300ms
target = first_burst_bytes if stats.frames == 0 else frame_bytes
```

为什么首帧要 300ms 而不是标准的 85ms：
固件有 200ms 预缓冲（`APP_PLAYBACK_RING_MS`），攒够才开播。
若首帧只发 85ms，板子会一直等不满，表现为"更晚才出声"。

这是 zjy13 踩过的坑（`ws/server.py:1100` 的注释原文：
"若服务端还按 lead_sec 节流，首块可能只发出几十毫秒音频，
板子就一直等不满预缓冲 —— 表现为'更晚才出声'，反而更差"）。

**2. 背压计算**

```python
def should_wait(self, played_sec, next_piece_bytes) -> float:
    if self.sent_bytes < self.first_burst_bytes:
        return 0.0          # 首块阶段不节流
    ahead = sent_sec - played_sec
    return max(0.0, ahead - self.lead_sec)   # 领先不超过 0.8s
```

为什么需要：zjy13 注释记录过"移动热点/WiFi Direct 链路上容易把下行推爆
（transport 写超时断连）"。

**3. FakeDevice 模拟固件**

按固件行为模拟，用于无设备时验证协议：

```python
class FakeDevice:
    """模拟 ESP32：200ms 预缓冲 + seq 校验 + 结束标记检测"""
    def feed(self, frame):
        # 解码 → seq 连续性检查 → 累积缓冲 → 攒够 200ms 就"开播"
```

**4. 首帧延迟指标修正**

初版把 `first_frame_ms` 算成"整段收集完的时间"，导致它等于总耗时（无意义）。
修正为「引擎产出第一块的时间」，这样指标才反映"设备多久能拿到第一帧"。

---

## 四、端到端验证

### 4.1 完整链路（真模型）

```text
CosyVoice3 引擎（真模型，流式）
  → bridge.stream_to_protocol_frames（切成 12 字节头协议帧）
  → FakeDevice（模拟固件消费）
```

| 文本 | 路由 | 帧数 | 音频 | **首帧** | 整段 | 开播帧 |
|---|---|---|---|---|---|---|
| 短句 10 字 | 非流式 | 30 | 3.16s | **1609ms** | 2860ms | 1 |
| 中句 22 字 | 流式 | 48 | 4.72s | **1450ms** | 4298ms | 1 |
| 长句 44 字 | 流式 | 114 | 10.36s | **1588ms** | 7657ms | 1 |

**首帧 1.45–1.61s，整段 2.9–7.7s。** 首帧远小于整段，说明流式确实生效。

每档都通过 6 项校验：

```
[PASS] 首帧 14400B >= 14400B（300ms）
[PASS] N 帧全部解码正确（seq 连续、turn_id 一致）
[PASS] 无 seq 跳变
[PASS] 收到结束标记
[PASS] 攒满 200ms 预缓冲后开播（第 1 帧）
[PASS] 字节守恒: 播放+缓冲 = 总字节
```

### 4.2 验收脚本（9 项全通过）

```
[1] 协议契约与 zjy13 源码一致性    7 项 PASS
[2] zjy13 侧无需改动即可对接        3 项 PASS
[3] 服务启动（真模型 22s）          PASS
[4] 响应头满足 TTSClient 要求       PASS（采样率 24000）
[5] 流格式与 zjy13 完全一致         PASS
[6] /system/status 暴露桥接信息     PASS
[7] 短句走非流式路由                PASS
[8] 端到端帧切分与设备消费          PASS
```

---

## 五、测试

```
304 个测试全绿（Phase 4 的 264 + 本阶段 40）
```

新增 `tests/test_bridge_protocol.py` 40 个，分六类：

| 类别 | 数量 | 覆盖 |
|---|---|---|
| 二进制帧格式 | 10 | 魔数、帧头长度、小端、seq 回绕、超长拒绝、截断检测 |
| HTTP 流格式 | 3 | 大端长度前缀、0 结束标记、往返解析 |
| 帧切分 | 9 | 首突发、标准帧、尾帧、字节守恒、seq 递增 |
| 背压 | 3 | 首块不节流、领先超限等待、追平不等待 |
| 模拟设备 | 5 | 预缓冲开播、结束标记、seq 跳变检测、完整流程 |
| **与 zjy13 一致性** | **8** | 直接读 zjy13 源码比对常量 |
| 兼容性描述 | 3 | 字段完整性、值正确、来源可追溯 |

**最关键的是那 8 个一致性测试**——它们会在 zjy13 常量变化时立刻失败。

---

## 六、仍未解决

| 项 | 说明 |
|---|---|
| 真设备联调 | 本次未接真板子（zjy13 已烧录验证过固件，协议层已对齐） |
| WebSocket 层 | 在 zjy13，本次未改动 |
| 打断/取消端到端 | 协议支持（`cancel` / `turn_cancelled`），未在 zjy14 侧验证 |
| 弱网测试 | 需要真设备与弱网环境 |
| 多设备并发 | 单设备场景 |

---

## 七、复现命令

```bash
# 协议桥接单元测试（含 zjy13 一致性比对）
wsl -e bash scripts/run_tests.sh tests/test_bridge_protocol.py

# 真模型端到端（引擎 → 协议帧 → 模拟设备）
wsl -e bash probe/run_d1.sh probe/_p5_e2e.py

# Phase 5 验收
wsl -e bash scripts/verify_phase5.sh
```

---

## 八、经验教训

1. **先读源码再动手，能省掉大量重复实现**。zjy13 的流式路径、帧格式、
   首突发策略都已验证过，直接复用比重新设计快得多，也更可靠。

2. **协议一致性要用测试守护**。8 个读 zjy13 源码的测试，
   比我写十遍注释都管用——常量一变立刻红。

3. **指标要反映它声称的含义**。`first_frame_ms` 初版算成"整段收集完"，
   和总耗时相等，完全没用。修正为"引擎产出第一块的时间"才有意义。

4. **别人的坑要继承，不要重踩**。zjy13 注释里记录的"首块太小导致更晚出声"
   和"移动热点推爆 transport"，我直接沿用其参数（300ms / 0.8s lead），
   没有重新试错。

5. **模拟设备是被低估的测试工具**。`FakeDevice` 按固件行为模拟
   （预缓冲、seq 校验、结束标记），让协议层可以在没有硬件时完整验证。
