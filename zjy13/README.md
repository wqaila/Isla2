# AI-Role · ESP32-S3 智能角色语音交互系统

> 架构：**本地 ASR + 云端 LLM + 本地 TTS**（V2 方案）
> 当前角色：**爱莉希雅（Elysia）**，音色来自真实角色语音素材克隆。
> 本文件记录的是**实际跑通的状态**，未验证的部分均明确标注。

---

## 一、项目进度

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| — | 云端 LLM 模块 | ✅ 完成，真实 DeepSeek 验证 |
| 一 | 本地 ASR（FunASR Paraformer） | ✅ 完成，RTF 0.009 |
| 二 | 云端 LLM 选型与联调 | ✅ 完成，deepseek-flash |
| 三 | 本地 TTS（CosyVoice3）+ 终端闭环 | ✅ 完成，端到端约 4~5 秒 |
| 四 | WebSocket 通信 | ✅ 完成，10 轮连续对话通过 |
| 四.五 | **角色音色克隆（爱莉希雅）** | ✅ 完成，多音色注册 + 全链路生效 |
| 五 | ESP32 固件 | ✅ 代码完成、编译通过（196KB）、协议验证通过；**未烧录** |
| 六 | 整机联调 | ⬜ 未开始 |

---

## 二、整体架构

```
┌──────────────┐   WebSocket    ┌─────────────────────────────────┐
│ ESP32-S3     │◄──────────────►│ Windows 电脑                     │
│ 录音/显示/播放 │   JSON + 二进制 │                                 │
└──────────────┘                │  ASR  FunASR Paraformer（GPU）   │
                                │  LLM  DeepSeek 云端 API          │
                                │  TTS  CosyVoice3（WSL2 / GPU）   │
                                └─────────────────────────────────┘
```

| 模块 | 环境 | 依赖 |
| --- | --- | --- |
| ASR | Windows `.venv-asr` | torch 2.8.0+cu128、FunASR 1.4.16 |
| LLM | Windows `.venv` | httpx、PyYAML |
| TTS | WSL2 `~/cosyvoice-venv` | torch 2.8.0+cu128、CosyVoice3 |
| WebSocket | Windows `.venv-asr` | websockets 12.0 |

---

## 三、角色与音色

### 3.1 角色卡

`server/characters/elysia.yaml` —— 爱莉希雅（《崩坏3》真我之律者）。

| 项 | 值 |
| --- | --- |
| 角色 | 爱莉希雅（Elysia） |
| 对用户的称呼 | 舰长 |
| 系统提示词长度 | 1816 字 |
| 示例对话 | 7 组 |
| 绑定音色 | `elysia` |
| 版本 | 1.0 |

保留的原角色卡：`default.yaml`（小满），可随时切换回去。

### 3.2 音色克隆

参考音频来自 `C:\Users\user\Desktop\aili` 的 7 段短音频 + 1 段 21 分钟角色语音合集。

**处理流程**：

```
21 分钟素材
  ↓ tools/prepare_voice_ref.py scan   自动切段 + 打分 + ASR 转写
92 个候选段（按信噪比/时长/削波打分）
  ↓ 人工选定：174.89s~184.59s，9.70s，SNR 60.8dB
归一化到 -3dBFS，导出 24kHz/16bit/单声道
  ↓ ASR 回读校验（文本与音频逐字对齐）
server/characters/voices/elysia/ref_elysia.wav + ref_elysia.txt
  ↓ 服务启动时注册到 CosyVoice3
voice_id = "elysia"
```

**参考文本**：

> 我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀

**实测效果**（真实合成）：

| 文本 | 音频时长 | 合成耗时 | RTF |
| --- | --- | --- | --- |
| 嗯哼~舰长，你好呀！今天也是元气满满的一天呢！ | 5.40s | 3.14s | 0.58 |
| 舰长今天辛苦了，要不要休息一下呢？ | 6.00s | 3.50s | 0.58 |

### 3.3 多音色

服务启动时扫描 `server/characters/voices/` 下每个子目录，自动注册：

| voice_id | 来源 | 说明 |
| --- | --- | --- |
| `xiaoman` | CosyVoice 内置示例音频 | 兜底音色 |
| `elysia` | 角色语音素材克隆 | 当前默认 |

新增音色只需建一个子目录，放入 `ref_*.wav` 和同名 `.txt`，重启服务即可。

---

## 四、环境（本机实测）

| 项 | 值 |
| --- | --- |
| 操作系统 | Windows 10.0.26100 |
| Python（LLM/TTS 侧） | 3.11.9，`zjy13/.venv` |
| Python（ASR 侧） | 3.11.9，`zjy13/.venv-asr` |
| Python（TTS 侧） | 3.10.22，WSL2 内 `~/cosyvoice-venv` |
| GPU | NVIDIA GeForce RTX 5070，12227 MiB，**sm_120** |
| CUDA | 12.8 |
| PyTorch | 2.8.0+cu128（三处环境统一） |
| WSL | Ubuntu-24.04 |

### 关键约束：sm_120

RTX 5070 是 Blackwell 架构，**必须用 CUDA 12.8+ 构建的 PyTorch ≥ 2.7.0**。

陷阱：架构不匹配时 `torch.cuda.is_available()` 仍返回 `True`，简单矩阵乘也能算出结果（走 PTX JIT），极易误判为"GPU 正常"。因此代码里额外校验 `get_arch_list()`。

---

## 五、安装与运行

```powershell
cd D:\xiangmudaima\zjy13
```

### 1. 配置

```powershell
Copy-Item .env.example .env
notepad .env   # 填写 LLM_BASE_URL / LLM_API_KEY
```

关键配置项：

```ini
DEFAULT_CHARACTER_ID=elysia
TTS_VOICE_ID=elysia
LLM_MODEL=deepseek-flash
LLM_THINKING=disabled
```

### 2. 启动 TTS 服务（WSL2，单独终端）

```powershell
wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/serve_tts_wsl.sh
```

首次启动约 15~50 秒（加载模型 + 注册音色 + 预热）。

健康检查：

```powershell
curl http://127.0.0.1:50000/health
```

```json
{
  "ok": true,
  "engine": "cosyvoice3",
  "sample_rate": 24000,
  "device": "cuda",
  "voices": [
    {"voice_id": "xiaoman", "is_builtin": true},
    {"voice_id": "elysia", "is_builtin": false}
  ]
}
```

### 3. 跑完整闭环

```powershell
# 文本模式（跳过 ASR）
.\.venv\Scripts\python.exe -m tools.dialogue_loop --text-mode

# 音频模式
.\.venv-asr\Scripts\python.exe -m tools.dialogue_loop "tests\audio\asr_example.wav"
```

### 4. WebSocket 服务（给开发板用）

```powershell
.\.venv-asr\Scripts\python.exe -m server.ws.server --host 127.0.0.1 --port 8765
```

用模拟器测试：

```powershell
.\.venv-asr\Scripts\python.exe -m tools.ws_client_sim --rounds 10
```

### 5. 单元测试

```powershell
# LLM / TTS / WebSocket / 音色
.\.venv\Scripts\python.exe -m pytest tests -q

# ASR（需要 torch）
.\.venv-asr\Scripts\python.exe -m pytest tests -q
```

实测：**`.venv` 262 passed**，**`.venv-asr` 272 passed**。

> `conftest.py` 会在缺少 torch 时自动跳过 ASR 引擎测试，
> 因此两个环境都能用一条命令跑完，不会在收集阶段中断。

---

## 六、实测数据

### 6.1 端到端延迟

| 阶段 | 耗时 | 占比 |
| --- | --- | --- |
| ASR | 106 ms | 2.5% |
| LLM 首字 | 909 ms | — |
| LLM 完成 | 1142 ms | 27.0% |
| TTS | 2981 ms | 70.5% |
| **端到端** | **约 4.2~5.0 秒** | |

**TTS 是绝对瓶颈**（LLM 占 TTS 内部 79%）。

### 6.2 ASR

```
识别: 欢迎大家来体验达摩院推出的语音识别模型
推理 49.7 ms | RTF 0.009 | 模型加载 1695 ms | 预热 456 ms
```

RTF 0.009 = 比实时快 111 倍，**ASR 不是瓶颈**。

### 6.3 LLM（deepseek-flash，thinking=disabled）

| 指标 | deepseek-flash | deepseek-v4-pro |
| --- | --- | --- |
| 首字延迟 mean | 646 ms | 877 ms |
| 首字延迟 P95 | 984 ms | 1153 ms |
| 总耗时 mean | 996 ms | 1202 ms |

### 6.4 TTS（CosyVoice3-0.5B）

| 指标 | 值 |
| --- | --- |
| 模型加载 | 9.2~11.2 s |
| 预热 | 3.4 s |
| 合成 RTF | 0.58~0.70 |
| 峰值显存 | 3.57 GB |

### 6.5 WebSocket

```
成功 10/10 轮（此前）
端到端均值 6491 ms
累计音频 4544640 字节
0 错误，无串轮，无音频错序
```

本次 5 轮（角色=爱莉希雅，音色=elysia）：

```
成功 5/5 轮
端到端均值 8092 ms
端到端最大 9824 ms
显存 7274 → 7228 MiB（无泄漏）
```

### 6.6 显存并发（V2 第十五节硬要求）

| 状态 | 显存 |
| --- | --- |
| ASR + TTS 同时驻留 | **7286 MiB / 12227 MiB** |
| 仅 TTS | 6177 MiB |
| 推算 ASR | 约 1.1 GB |
| 剩余可用 | 约 4.9 GB |

**结论：12 GB 显存足够，不构成并发风险。**

---

## 七、配置参数依据

| 参数 | 值 | 依据 |
| --- | --- | --- |
| `LLM_REQUEST_TIMEOUT_S` | 20 | 实测 P95 总耗时 1.3s |
| `LLM_TOTAL_TIMEOUT_S` | 45 | 语音场景超过 45s 无交互意义 |
| `LLM_MAX_ATTEMPTS` | 3 | 含首次，累计退避约 1.5s |
| `LLM_BACKOFF_MAX_S` | 4 | 退避上限 |
| `LLM_MAX_TOKENS` | 256 | 约 170 汉字，够 1~5 句且拦住长篇 |

---

## 八、开发中修复的关键问题

| # | 问题 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | 角色卡口头禅变成固定开头 | 语义与"不确定"绑定 | 新增 `catchphrase_usage` 约束 |
| 2 | 把"没有长期记忆"误解成"看不见历史" | 角色卡未说明上下文可见 | 新增【本次对话的上下文】段落 |
| 3 | 空历史时编造用户经历 | 纯提示词约束不可靠 | 结构性拦截（`guards.py`） |
| 4 | 重置后 turn_id 重复 | 未换 session_id | 重置时重新生成 |
| 5 | TTS `token_hop_len` 跨调用污染 | 上游实例属性不重置 | 每次合成前重置为 25 |
| 6 | fp16 反而更慢 | 瓶颈在 kernel 调度 | 固定 fp32 |
| 7 | **参考文本缺 `<|endofprompt|>` 前缀** | LLM 前缀错位，token 序列过短 | 服务端自动补前缀 |
| 8 | 音色扫描把 90% 素材判成静音 | 阈值按全局峰值算 | 改为相对噪声底 |
| 9 | 测试在收集阶段整体中断 | `.venv` 缺 numpy | 补齐依赖 + `conftest.py` 精确跳过 |
| 10 | **WS 服务端不传 `voice_id`** | 开发板音色与终端不一致 | 从角色卡解析后传入 |
| 11 | **WS 服务端硬编码小满角色卡** | 与 `.env` 的 elysia 冲突 | 改读 `DEFAULT_CHARACTER_ID` |

### 问题 7 详解（最隐蔽的一个）

音色注册**成功**、健康检查**正常**、参考音频格式**完全正确**，但每次合成必然报：

```
RuntimeError: Calculated padded input size per channel: (3).
Kernel size: (4). Kernel size can't be greater than actual input size
```

定位过程：同文本 `xiaoman` 成功 / `elysia` 失败 → 换文本仍失败 → 换音频不换文本仍失败 → 确认是**参考文本**问题。

根因：CosyVoice3 的 `prompt_text` 必须是

```
You are a helpful assistant.<|endofprompt|><参考音频文本>
```

缺前缀时 LLM 前缀错位，生成阶段拿到过短 token 序列，最终在卷积层崩溃。

---

## 九、安全说明

- 密钥只从环境变量/`.env` 读取，代码中无硬编码；`.env` 已忽略。
- 日志只记录 `api_key_set: True/False`，不记录密钥值（有测试断言）。
- 角色卡与用户输入均作为数据进入 `user` 消息，历史中的 `system` 会被丢弃。
- `httpx` 使用 `trust_env=False`，避免系统代理把密钥转发到未知节点。
- TTS 服务只监听 `127.0.0.1`（V2 第六节第 10 条）。
- TTS 不可用时**明确报错，不静默回退云端**（V2 第六节第 7 条）。

---

## 十、目录结构

```
zjy13/
├─ .env.example
├─ requirements.txt
├─ conftest.py                  # pytest 配置（缺 torch 时跳过 ASR 测试）
├─ server/
│  ├─ config.py                 # LLMConfig
│  ├─ llm/                      # 云端 LLM：适配器/流式/重试/去重
│  ├─ dialogue/                 # 角色卡/会话/守卫/文本处理
│  ├─ tts/client.py             # TTS HTTP 客户端
│  ├─ protocol/                 # WebSocket 消息协议
│  ├─ ws/                       # WebSocket 服务端 + 音频处理 + 状态机
│  └─ characters/
│     ├─ elysia.yaml            # 爱莉希雅（当前默认）
│     ├─ default.yaml           # 小满（保留）
│     └─ voices/
│        └─ elysia/
│           ├─ ref_elysia.wav   # 9.70s / 24kHz / 16bit / 单声道
│           └─ ref_elysia.txt   # 参考文本（ASR 回读校验过）
├─ asr/                         # FunASR 封装 + 音频工具
├─ firmware/                    # ESP32-S3 固件（阶段五，196KB）
│  ├─ CMakeLists.txt
│  ├─ partitions.csv            # 4MB app + 4MB storage
│  ├─ sdkconfig.defaults
│  └─ main/
│     ├─ main.c                 # 入口 + 消息分发 + 录音/播放任务
│     ├─ app_board.h            # 板级引脚（官方 BSP 核实）
│     ├─ app_config.h           # 协议常量（与服务端对齐）
│     ├─ app_protocol.h/.c      # 二进制帧 + 轻量 JSON
│     ├─ app_net.h/.c           # Wi-Fi + WebSocket（退避重连）
│     ├─ app_audio.h/.c         # I2S 录音/播放（半双工）
│     ├─ app_ui.h/.c            # 屏幕显示（默认关闭）
│     └─ app_state.h/.c         # 状态机
├─ scripts/
│  ├─ tts_server_wsl.py         # CosyVoice3 HTTP 服务（多音色注册）
│  ├─ serve_tts_wsl.sh
│  └─ flash_firmware.ps1        # 烧录 / 还原原厂固件
├─ tools/
│  ├─ prepare_voice_ref.py      # 音色参考音频挑选/导出
│  ├─ dialogue_loop.py          # 完整语音闭环
│  ├─ ws_client_sim.py          # WebSocket 协议模拟器
│  ├─ fw_protocol_equiv.py      # 固件字节级协议等价验证
│  └─ ...
├─ tests/                       # 262 + 272 个测试
├─ docs/                        # 17 篇技术文档
└─ backup/esp32/                # 原厂固件备份（16MB，3 份副本）
```

---

## 十一、未验证项（如实标注）

### 硬件

- [ ] **固件未烧录**：只编译过，没上真实硬件
- [ ] I2S 录音（ES7210 寄存器序列来自官方 BSP，未实测）
- [ ] 开发板播放 24000 Hz（BSP 默认 22050，需确认）
- [ ] 4 通道 TDM 实采、半双工切换
- [ ] 麦克风音质、扬声器、回声处理、电池
- [ ] 真实 Wi-Fi 下的丢包/乱序/重连

### 软件

- [x] ~~ASR + TTS 同时驻留显存~~ → **已实测：7286 MiB / 12227 MiB，5 轮无泄漏**
- [x] ~~音色绑定到 WebSocket 链路~~ → **已修复并验证合成使用 `elysia`**
- [ ] 长音频（> 1 分钟）连续识别
- [ ] 噪声环境
- [ ] 打断/取消机制的端到端验证

### 音质

- [ ] **爱莉希雅克隆音色的主观听感**（需人工试听）
- [ ] 与原始角色声音的相似度评分
- [ ] 长句合成时的稳定性

---

## 十二、下一步

1. **烧录固件**到真实硬件（阶段五收尾）

   ```powershell
   idf.py menuconfig          # 填 Wi-Fi SSID/密码、服务端 IP
   .\scripts\flash_firmware.ps1 -Port COM7 -Build
   ```

   建议按 `docs/firmware.md` 第十二节的顺序调：网络 → 文字 → 录音 → 播放 → 半双工。

2. **试听验证**：`logs/tts/*.wav` 里的合成音频，确认音色像不像爱莉希雅

3. **整机联调**（阶段六）

> 固件已通过协议一致性验证（54 个静态测试 + 字节级等价测试），
> 但**音频链路完全未在真实硬件上验证**，I2S 部分最可能出问题。
