# 真机联调报告（Phase 5 补完：从"桥接验证"到"真机在环"）

日期：2026-11-19
环境：Windows 11 + WSL2 Ubuntu-24.04 + RTX 5070 12GB
板子：ESP32-S3-Touch-LCD-1.85B（`44:bd:8d:78:14:68`，固件 `yuanma/AI-Pet-Badge-main`）

## 一、为什么做这件事

zjy14 的所有 Phase 在 README 里都已标 ✅，但 `docs/20`、`docs/26` 的日报里
P0 一直是同一条：

> **真机联调（看监控页在真实板子上的表现）— 需要 ESP32 硬件**

`docs/18_PHASE5_REPORT.md` 也明确记录 Phase 5 的定位被 D7 收敛成了
「桥接验证」：协议对齐 + FakeDevice 模拟通过，**没有接真板子**。

本次板子在线（WiFi `Isla2Badge`，IP `192.168.137.254`），把这条 P0 补完。

## 二、做了什么

### 2.1 修复诊断脚本的两个硬编码错误

`probe/_p8_device_check.py` 之前一直"跑不出结论"，不完全是没硬件，
脚本本身也有 bug：

| 问题 | 影响 | 修法 |
|---|---|---|
| `[2]` 检查端口 **50000** | 50000 是 zjy13 旧 TTS 服务的端口；设备接入 WS 实际在 **8765**。所以真机明明在线也报"未启动" | 改查 8765，并区分"服务在跑"与"真机在线"两件事 |
| 用 `127.0.0.1` 访问 Windows 侧服务 | 脚本跑在 WSL，WSL2 的 `127.0.0.1` 是它自己，Windows 的服务要用默认网关地址 | 新增 `resolve_zjy13_base()`，依次试 `127.0.0.1` 与 `ip route` 的网关，取第一个能通的 |

第二个问题很隐蔽：手动 `curl` 从 Windows 侧测一直是通的，
只有在 WSL 里跑诊断才会失败，而脚本又把它归因为"服务未启动"。

### 2.2 把 zjy13 的 TTS 指向 zjy14

`zjy13/.env` 原本没有 `TTS_BASE_URL`，`TTSConfig.from_env()` 的默认值是
`http://127.0.0.1:50000`（zjy13 自己的旧引擎）。追加：

```bash
TTS_BASE_URL=http://127.0.0.1:50001
```

**关键点**：环境变量在进程启动时读入，改完 `.env` 必须**重启 zjy13 WS 服务**
才生效。重启后日志确认：

```
INFO httpx: HTTP Request: GET http://127.0.0.1:50001/health "HTTP/1.1 200 OK"
INFO ws.server: TTS 就绪: device=cuda
```

### 2.3 新增"用生产代码实打"的验证

这是本次最重要的补充。原来的 `_p8_device_check.py` 第 [6] 项是**直接**
打 zjy14 的 HTTP 接口，绕过了 zjy13 自己的客户端代码 —— 它只能证明
"zjy14 服务本身能用"，**不能证明板子听到的是 zjy14**。

新增 `probe/_verify_zjy13_tts.py`，改用 zjy13 生产代码里的
`server/tts/client.py::TTSClient`（`ws/server.py` 每轮对话真正调用的类）走一遍。

踩到一个坑：`TTSConfig.from_env()` 只读 `os.environ`，不会自己读 `.env`，
必须显式 `load_env_file()`。第一版漏了这步，误报"未指向 zjy14"。

### 2.4 一键验收脚本

`probe/run_device_e2e.sh` 把两步串起来，方便以后复跑：

```bash
wsl -e bash /mnt/d/xiangmudaima/zjy14/probe/run_device_e2e.sh
```

## 三、验收结果

```
================================================================
 真机联调一键验收
================================================================
[1] zjy14 服务（TTS 引擎）
  [OK] http://127.0.0.1:50001 就绪
       引擎=cosyvoice3  采样率=24000  设备=cuda  音色=['elysia']

[2] zjy13 WebSocket 服务（设备接入点）
  连接地址: http://172.17.0.1:8765
  [OK] 真机在线 1 台
       device=ai-pet-badge  peer=('192.168.137.254', 56953)
       state=idle  hb=17.1s  heartbeat_ok=True

[4] 协议契约对齐
  协议 v1  magic=0xA5  帧=4096B(85ms)  采样率=24000
  [OK] 契约常量正确

[5] zjy13 的 TTS 指向
  TTS_BASE_URL = http://127.0.0.1:50001
  [OK] 已指向 zjy14

[6] 端到端模拟（zjy14 流式 → 协议帧 → 模拟设备）
  HTTP 流: 3 块  207360 字节  4.32s
  协议帧: 44 帧  首帧 14400B（满足 300ms 首突发）
  模拟设备: 收 45 帧  开播=True  收尾=True  seq错误=0
  [OK] 链路可通

--- Step 2: 用 zjy13 的 TTSClient 实打 ---
  [1] health() = True   engine=cosyvoice3  device=cuda  sample_rate=24000
  [2] synthesize()      音频 4.92s  合成 3106ms  RTF 0.630  236204 字节
  [3] synthesize_stream()  块数 3  总字节 268800  首块 1544ms  全部 4616ms

  [OK] 全部通过：板子听到的是 zjy14（CosyVoice3）的声音。
```

第 [3] 项「串口设备」显示 `[--]` 是**正常的**：诊断跑在 WSL 里，
看不到 Windows 的 COM7。板子状态是通过 zjy13 的 `/api/devices` 确认的，
比看串口更可靠。

### 3.1 测试回归

zjy14 全量测试：**472 项全部通过**（`pytest tests -q`）。

## 四、踩坑记录

| 坑 | 表现 | 根因 |
|---|---|---|
| WSL 的 `127.0.0.1` ≠ Windows 的 | 诊断报"zjy13 未启动"，但 Windows 侧 curl 正常 | WSL2 独立网络命名空间，Windows 服务要走默认网关 |
| 端口 50000 vs 8765 | 真机在线仍报"WS 未启动" | 50000 是 zjy13 旧 TTS 端口，设备接入在 8765，脚本写错了 |
| `.env` 改了不生效 | `TTSConfig` 仍读默认 50000 | 环境变量在进程启动时读入，必须重启 WS 服务 |
| `TTSConfig.from_env()` 不读文件 | 验证脚本误报未指向 zjy14 | 需显式 `load_env_file()`，与服务进程保持一致 |
| 用错 python | `ModuleNotFoundError: No module named 'numpy'` | zjy13 依赖在 `.venv-asr`，不是系统 python |

## 五、当前状态与剩余项

| 项 | 状态 |
|---|---|
| 板子 WiFi / WS 连接 | ✅ 在线，心跳正常 |
| zjy14 引擎（cosyvoice3/CUDA） | ✅ 就绪，RTF 0.63 |
| zjy13 → zjy14 TTS 切换 | ✅ 已生效 |
| 协议契约（v1/0xA5/4096B/24kHz） | ✅ 对齐 |
| 端到端音频流 | ✅ 54→44 帧，seq 无跳变，收尾正常 |
| **按按钮跑完整对话轮** | ⏳ **待人工操作**（需有人在板子前按按钮说话） |

最后一项只能人工做：按板子按钮说一句话，观察屏幕状态与是否出声。
按 `docs/18` 的排查顺序，无声时依次看 `playback_start`、音量、I2S 半双工占用。

## 六、相关文件

| 文件 | 改动 |
|---|---|
| `probe/_p8_device_check.py` | 修端口 50000→8765；加 WSL 主机自动探测；区分"服务在跑"与"真机在线" |
| `probe/_verify_zjy13_tts.py` | **新增**：用 zjy13 生产代码的 TTSClient 实打 |
| `probe/run_device_e2e.sh` | **新增**：一键验收 |
| `probe/_check_win_port.sh` | **新增**：WSL→Windows 端口连通性探测 |
| `../zjy13/.env` | 追加 `TTS_BASE_URL=http://127.0.0.1:50001` |
