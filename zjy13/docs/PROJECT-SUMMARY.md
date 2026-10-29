# AI 角色语音系统 · 开发总结

> 总结日期：2026-10-29
> 项目：ESP32-S3 智能角色语音交互系统（本地 ASR + 云端 LLM + 本地 TTS）
> 硬件：Waveshare ESP32-S3-Touch-LCD-1.85B（ESP32-S3R8，16MB Flash，8MB PSRAM）

---

## 一、项目目标

做一套完整的智能角色语音交互系统：板子采集语音，电脑做识别与理解，本地合成角色声音，最后回到板子播放。角色为《崩坏3》的**爱莉希雅（Elysia）**。

架构职责划分：

```
┌──────────────┐   Wi-Fi/WS    ┌─────────────────────────────────┐
│ ESP32-S3     │◄─────────────►│ 电脑                             │
│ 录音/显示/播放 │  JSON + PCM   │  ASR  FunASR（GPU）              │
└──────────────┘               │  LLM  DeepSeek 云端 API          │
                               │  TTS  CosyVoice3（WSL2 / GPU）   │
                               └─────────────────────────────────┘
```

板子**不做任何推理**，只负责采集、播放与显示。

---

## 二、完成情况总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| 一 | 本地 ASR（FunASR Paraformer） | ✅ RTF 0.009 |
| 二 | 云端 LLM（DeepSeek） | ✅ 真实调用验证 |
| 三 | 本地 TTS（CosyVoice3）+ 终端闭环 | ✅ 端到端约 4~5 秒 |
| 四 | WebSocket 协议与服务端 | ✅ 10 轮连续对话通过 |
| 四.五 | 角色音色克隆（爱莉希雅） | ✅ 真实素材克隆 |
| 五 | ESP32 固件 | ✅ 源码复现 + 语音客户端集成 |
| 六 | 整机联调 | ⬜ 受网络条件阻塞 |

---

## 三、关键成果

### 3.1 角色音色克隆

从 21 分钟角色语音素材中自动选段，生成零样本克隆参考音频：

| 项 | 值 |
|---|---|
| 素材 | 7 段短音频 + 1 段 21 分 20 秒合集 |
| 自动选段 | 92 个候选，按信噪比/时长/削波打分 |
| 最终选定 | 174.89–184.59s，9.70 秒，SNR 60.8dB |
| 校验方式 | ASR 回读，文本与音频逐字一致 |
| 实测合成 | RTF 0.58~0.70 |

**踩过的坑**：CosyVoice3 的参考文本必须带 `<|endofprompt|>` 前缀分隔符。缺少时音色注册「成功」、健康检查正常，但每次合成都在卷积层报错：

```
RuntimeError: Calculated padded input size per channel: (3).
Kernel size: (4).
```

定位过程用了四组对照实验，最终确认是参考文本问题而非音频问题。

### 3.2 原固件源码复现

从 16MB Flash 备份出发，最终完整复现原 `ai_pet_badge` 工程：

| 步骤 | 结果 |
|---|---|
| 源码 | 完整工程（`main.c`、`badge_scene.c`、`badge_imu.c` 等 16 个文件） |
| 动画素材 | 从 3 个绿幕视频重新生成，**288 帧 / 6.9MB**，官方校验 PASS |
| 编译 | 7944 KB，烧录地址 `0x20000` 与原版一致 |
| 分区表 | 与原备份完全一致（factory 9MB + model 5168K） |
| 启动 | 零 panic、零 assert |
| 实测 | `Assets v2 validated: 288 frames`，动画正常、触摸有反应 |

**视频辨识**：三个素材文件名是哈希，脚本要求 `walk.mp4`/`jump.mp4`/`sit.mp4`。通过逐帧分析人物高度与垂直重心变化判定：

- `walk`：高度稳定 0.83，垂直中心几乎不动 → 站立行走
- `jump`：高度 0.73→0.65 递减，重心 0.494→0.558 下移 → 跳跃
- `Sit`：重心 0.365→0.500 明显下移，包围盒底部持续下移 → 坐下

### 3.3 语音客户端集成

把 WebSocket 语音能力并入原固件工程，**复用 BSP 官方音频接口**而非手写寄存器：

| 项 | 说明 |
|---|---|
| 协议 | 12 字节帧头 + PCM，CRC32 与 Python `zlib.crc32` 一致 |
| 录音 | `esp_codec_dev_read()`，22050 Hz 立体声 |
| 播放 | `esp_codec_dev_write()`，24000 Hz 单声道 |
| 屏幕状态 | 五种状态显示（连接中/待命/在听你说/正在回应） |
| 串口命令 | `VOICE <ip> <port>`、`TALK`、`TALK STOP`、`VOICE STATUS` |

**屏幕状态设计**：不跨任务操作 LVGL，只写 `state.voice_state` + `revision++`，由已有 100ms 定时器统一刷新，避免显示锁竞争。

---

## 四、踩坑与修复记录

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | 角色卡口头禅变固定开头 | 语义与「不确定」绑定 | 新增 `catchphrase_usage` 约束 |
| 2 | 误以为「没长期记忆」=「看不见历史」 | 角色卡未说明上下文可见 | 新增上下文段落 |
| 3 | 空历史时编造用户经历 | 纯提示词不可靠 | 结构性拦截 `guards.py` |
| 4 | TTS `token_hop_len` 跨调用污染 | 上游实例属性不重置 | 每次合成前重置 |
| 5 | 音色扫描把 90% 素材判成静音 | 阈值按全局峰值算 | 改为相对噪声底 |
| 6 | 参考文本缺 `endofprompt` 前缀 | LLM 前缀错位 | 服务端自动补前缀 |
| 7 | WS 服务端不传 `voice_id` | 板子与终端音色不一致 | 从角色卡解析传入 |
| 8 | I2C 地址用 8 位写法 | `0x80` 超出 7 位上限 | 改为 `0x40` / `0x18` |
| 9 | 录音前不发 `recording_start` | 服务端无本轮上下文 | 先发请求等 `session_start` |
| 10 | I2S 切换不改时钟 | 只改 codec 寄存器 | 用 `reconfig_*_clock()` |
| 11 | USB 传输音频全失败 | `tx_buffer_size=1024` < 块 5120 | **确认不可行，改走 Wi-Fi** |
| 12 | 组件清单写死旧路径 | `dependencies.lock` 残留原作者路径 | 运行 `normalize_lock_paths.py` |

---

## 五、实测数据

### 5.1 延迟分解

| 阶段 | 耗时 | 占比 |
|---|---|---|
| ASR | 106 ms | 2.5% |
| LLM 完成 | 1142 ms | 27.0% |
| TTS | 2981 ms | 70.5% |
| **端到端** | **约 4.2~5.0 秒** | |

**TTS 是绝对瓶颈**。

### 5.2 显存并发（V2 硬要求）

| 状态 | 显存 |
|---|---|
| ASR + TTS 同时驻留 | 7286 MiB / 12227 MiB |
| 仅 TTS | 6177 MiB |
| 5 轮前后变化 | -46 MiB（无泄漏） |

### 5.3 固件对比

| 项 | 原厂 `ai_pet_badge` | 本次复现版 |
|---|---|---|
| 应用大小 | 7.32 MB | 7944 KB |
| 加入语音后 | — | 8695 KB |
| 动画帧数 | 248 帧 / 5.9MB | 288 帧 / 6.9MB |
| 分区布局 | factory 9MB + model 5168K | **完全一致** |

---

## 六、当前阻塞

### 网络不通（唯一阻塞项）

| 设备 | IP | 网段 |
|---|---|---|
| 电脑（手机 USB 共享） | `10.38.185.163` | 10.38.185.x |
| 板子（手机热点） | `10.246.194.130` | 10.246.194.x |

**两者不同网段，无法互访。** 已实测：
- 电脑 ping 板子 → 超时
- 电脑 ping 板子网关 → 通（1ms），说明网络实际连通，缺的是路由

**根因**：电脑**没有无线网卡，也没有蓝牙适配器**。板子无法通过任何无线方式连到主机。

**解决方式**：需 USB 无线网卡（30~50 元），让电脑连热点与板子同网段。

### USB 直连已排除

实测 USB-Serial/JTAG 传音频：
```
计划发送 250 块 × 5120 字节
实际成功 0 块
写入失败 250 次（100%）
```

固件 `tx_buffer_size=1024` 远小于音频块 5120 字节，且该通道本身不适合持续高吞吐。

---

## 七、待验证项

### 硬件

- [ ] 语音链路的真实录音与播放（I2C 探测已通过，实际音频未测）
- [ ] 麦克风音质、扬声器音量、回声处理
- [ ] 半双工切换是否有爆音
- [ ] 长时间运行稳定性

### 软件

- [ ] 真实网络下的丢包/乱序/重连
- [ ] 爱莉希雅克隆音色的主观听感（需人工试听）
- [ ] 打断/取消机制端到端

### 已知限制

- 无 OTA（仅 USB 重烧）
- 明文 `ws://`，无 TLS
- 触摸仍走串口命令触发录音，未做屏幕按键

---

## 八、仓库内容说明

### 包含

| 目录 | 内容 |
|---|---|
| `zjy13/` | 电脑侧服务端：ASR、LLM、TTS、WebSocket、角色卡、音色 |
| `zjy13/firmware/` | 第一阶段独立固件（已被 `yuanma` 版本取代，保留作参考） |
| `zjy13/docs/` | 18 篇技术文档，含审查清单与实测记录 |
| `zjy13/tools/` | 音色选段、协议模拟器、固件等价验证等 |
| `yuanma/AI-Pet-Badge-main/` | **原固件完整源码**（含 Elysia 动画、触摸、IMU、天气） |
| `yuanma/*.mp4` | 3 个绿幕动画源素材（walk/jump/sit） |

### 已排除（含敏感信息或体积过大）

| 文件 | 原因 |
|---|---|
| `zjy13/.env` | 含 DeepSeek API 密钥 |
| `yuanma/.env.wifi` | 含 Wi-Fi 密码 |
| `**/wifi_config.h` | 由 `.env.wifi` 生成，含密码 |
| `zjy13/backup/esp32/*.bin` | 16MB×3 固件备份，且**可能含原设备 Wi-Fi 凭据** |
| `zjy13/_models/`、`_wheels/` | 模型与安装包，体积大 |
| `**/build/`、`**/managed_components/` | 构建产物 |
| `**/animations.badge` | 6.9MB 生成物，可由视频重建 |

> ⚠ **重要**：16MB 固件备份内含原 `ai_pet_badge` 的 Wi-Fi 凭据，**不要上传到公开仓库**。本地保留即可。

---

## 九、复现步骤

### 电脑侧服务端

```powershell
cd zjy13

# 1. TTS（WSL2）
wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/serve_tts_wsl.sh

# 2. WebSocket 服务（需局域网可达时用 0.0.0.0）
.\.venv-asr\Scripts\python.exe -m server.ws.server --host 0.0.0.0 --port 8765
```

### 固件

```powershell
cd yuanma/AI-Pet-Badge-main

# 1. 配置 Wi-Fi
Copy-Item .env.wifi.example .env.wifi
notepad .env.wifi   # 填 WIFI_SSID / WIFI_PASSWORD
python tools/configure_badge_network.py

# 2. 生成动画素材（需要三个 mp4）
python tools/prepare_badge_assets.py --source ../ --output firmware/ai_pet_badge/main/animations.badge --preview-dir artifacts/assets/preview --fps 24

# 3. 路径归一化（换机器必须做）
python tools/normalize_lock_paths.py

# 4. 编译烧录
. .\tools\Enter-Idf.ps1
idf.py -C firmware/ai_pet_badge -D BADGE_CHARACTER=elysia build
idf.py -C firmware/ai_pet_badge -p COM7 flash
```

### 板子侧语音命令

```
VOICE <电脑IP> 8765    # 连接服务端
TALK                   # 开始录音
TALK STOP              # 结束，等待回复
VOICE STATUS           # 查看状态
```

---

## 十、下一步

1. **解决网络**：装 USB 无线网卡，让电脑与板子同网段
2. **验证语音链路**：真实录音 → ASR → LLM → TTS → 板子播放
3. **优化**：TTS 占 70% 延迟，可考虑流式合成
4. **产品化**：触摸触发录音、屏幕显示对话内容

> 当前固件已具备完整语音能力，只差网络连通即可端到端验证。
