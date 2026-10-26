# V2 方案转向分析（本地 TTS 版）

> 对照文档：`ESP32-S3_智能角色语音交互项目开发提示词_V2.0_本地TTS版.txt`
> 本文只做**方向确认与风险核实**，所有技术结论均来自本机实测或官方仓库原文，不臆测。

---

## 一、架构变化：只改一处，但影响很大

| 模块 | V1 方案 | V2 方案 | 变化 |
| --- | --- | --- | --- |
| ASR | 本地 FunASR | 本地 FunASR | 不变 |
| LLM | 云端 API | 云端 API | 不变 |
| **TTS** | **云端 API** | **本地模型** | **改变** |
| 通信 | WebSocket | WebSocket | 不变 |
| 固件 | ESP32-S3 | ESP32-S3 | 不变 |

V2 原文第 4 行明确约束：

> 总体架构约束：本地 ASR + 云端 LLM + 本地 TTS。
> 不得将 LLM 擅自改成本地部署，也不得让 TTS 默认调用云端 API。

**因此已有成果的处置**：

- `server/llm/` 云端 LLM 模块 —— **完全保留**，V2 第四节要求与现有一致
- `server/dialogue/` 角色卡与会话 —— **保留**，V2 第七节要求一致
- `asr/` 本地 ASR —— **保留**，V2 第三节要求一致
- TTS 模块 —— **从未实现**（V1 阶段也没做），现在按 V2 用本地模型实现

**结论：不需要推翻重做，只需新增本地 TTS 模块。** 已有代码全部符合 V2。

---

## 二、V2 新增的硬性要求（V1 没有的）

| # | 要求 | 影响 |
| --- | --- | --- |
| 1 | TTS 必须本地推理，不得调用云端 | 新增本地 TTS 模块 |
| 2 | 本地 TTS 优先 HTTP API，仅监听回环 | 需起本地服务 |
| 3 | 记录模型版本、来源、许可证、路径 | 需版本台账 |
| 4 | 不得伪称不支持的控制已生效 | 参数能力需实测 |
| 5 | TTS 音频需转换为开发板可播放格式 | 需重采样/格式转换 |
| 6 | 合成超时、队列、取消、防重复合成 | 状态机增强 |
| 7 | **TTS 加载失败不得偷偷回退云端** | 错误处理策略 |
| 8 | **实测 ASR 与 TTS 同时运行的显存竞争** | 需压测 |
| 9 | TTS 端口只监听 127.0.0.1 | 安全配置 |

其中第 8 条（ASR/TTS 显存竞争）是 V1 完全没有的新验收项，也是本地 TTS 最大的工程风险。

---

## 三、CosyVoice 3.0 可行性核实（实测官方仓库）

V2 第六节要求"必须核对所选版本的官方仓库说明"，以下是核实结果。

### 3.1 资源确实存在

| 项 | 实测结果 |
| --- | --- |
| ModelScope `FunAudioLLM/Fun-CosyVoice3-0.5B-2512` | ✅ HTTP 200，下载量 326,858 |
| GitHub `FunAudioLLM/CosyVoice` | ✅ 24,131 stars，最后推送 2026-05-25 |
| 备选 `iic/CosyVoice-300M` | ✅ HTTP 200，下载量 1,955,727 |

### 3.2 ⚠️ 但官方安装说明只覆盖 Linux

官方 README 的安装段落原文：

```sh
conda create -n cosyvoice -y python=3.10
conda activate cosyvoice
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# If you encounter sox compatibility issues
# ubuntu
sudo apt-get install sox libsox-dev
# centos
sudo yum install sox sox-devel
```

**全文没有任何 Windows 安装指引。**

### 3.3 requirements.txt 里的平台信号

```
deepspeed==0.15.1; sys_platform == 'linux'
onnxruntime-gpu==1.18.0; sys_platform == 'linux'
onnxruntime==1.18.0; sys_platform == 'darwin' or sys_platform == 'win32'
tensorrt-cu12==10.13.3.9; sys_platform == 'linux'
torch==2.3.1
torchaudio==2.3.1
```

解读：

- 项目**确实考虑了 Windows**（`onnxruntime` 有 win32 分支），说明不是完全不支持
- 但 Windows 上只能走 **CPU 版 onnxruntime**，GPU 加速的 `onnxruntime-gpu` 和 `tensorrt` 都是 Linux 专属
- 依赖里有 `sox`（需要系统库）、`pyworld`（需要 C 编译）、`openai-whisper`（需要编译），**Windows 上装这些大概率要 MSVC 构建工具链**
- 锁定 `torch==2.3.1`，而我们的 RTX 5070 是 sm_120，**torch 2.3.1 不支持**（需 ≥2.7.0）

### 3.4 三个硬冲突

| 冲突 | 现状 | 影响 |
| --- | --- | --- |
| **Python 版本** | CosyVoice 要求 3.10；本机 3.11.9（另有 3.10.9 可用） | 可用 3.10 建环境，但需独立 venv |
| **PyTorch 版本** | CosyVoice 锁 2.3.1；sm_120 需 ≥2.7.0 | **直接冲突**，需改依赖或打补丁 |
| **sox 系统库** | Linux 用 apt/yum 安装；Windows 需手工装 exe | 需额外步骤 |

### 3.5 WSL2 备选方案也受阻

V2 提到可用 WSL2。实测：

```
wsl --status  → Wsl/E_ACCESS_DENIED
```

当前执行环境**无法访问 WSL**（沙箱限制）。你本人在终端里可能可以，但需要你确认。

---

## 四、路线判断

### 不建议直接硬上 CosyVoice 3.0 的原因

1. **torch 版本冲突是硬伤**：CosyVoice 要 2.3.1，sm_120 要 ≥2.7.0。改 CosyVoice 的依赖可能引入不兼容。
2. **Windows 路径官方未验证**：V2 第 114 行明确说"不得宣称尚未验证的 Windows 安装方式一定可用"。
3. **依赖链重**：sox + pyworld + whisper 在 Windows 上编译，失败风险高。

### 建议的三条路线（按风险排序）

**路线 A：先做接口与链路，TTS 引擎后置（推荐）**

先把 V2 要求的**架构骨架**搭起来：
- 定义统一 TTS 接口 `synthesize(text, voice_id, speed) -> AudioResult`
- 实现本地 HTTP 服务（FastAPI，仅监听 127.0.0.1）
- 用一个**已验证可用的轻量本地 TTS** 先打通链路（如 Windows SAPI 或 piper-tts）
- CosyVoice 作为"引擎插件"后置接入

好处：链路先闭环，符合 V2"先完成一个模型的独立推理和本地 API 服务，再接入语音交互链路"的顺序要求，同时不被 CosyVoice 的环境问题卡死。

**路线 B：用 Python 3.10 独立环境直接试 CosyVoice**

按官方 requirements 装，torch 用 2.7.0+cu128 替换 2.3.1，看能否跑通。
风险：依赖冲突可能无解，预计耗时较长。

**路线 C：WSL2 + Ubuntu 跑 CosyVoice**

最贴合官方支持路径，但需你确认 WSL 可用，且要处理 GPU 直通和跨系统 HTTP 调用。

---

## 五、轻量备选：Windows 系统 TTS 实测情况

路线 A 需要一个"已验证可用"的轻量引擎先打通链路。实测本机：

| 项 | 结果 |
| --- | --- |
| SAPI 已安装语音 | 2 个：`Microsoft Huihui Desktop`(zh-CN)、`Microsoft Zira Desktop`(en-US) |
| 注册表 OneCore 语音 | 另有 `HuihuiM`、`KangkangM`、`YaoyaoM` 三个中文语音 |
| 合成到 WAV | ❌ 当前执行环境报"系统上未安装语音，或没有当前安全设置可用的语音" |

**判断**：系统**有中文语音**，但当前沙箱执行环境不允许调用语音引擎。
你在自己的终端里大概率可用（这是 Windows 自带能力，不需要额外安装）。

这条路线值得你本地验证一句：

```powershell
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice("Microsoft Huihui Desktop")
$s.SetOutputToWaveFile("D:\test.wav")
$s.Speak("今天天气不错")
$s.Dispose()
```

能出声 → 路线 A 的兜底引擎就有了，可立即打通链路。

---

## 六、我建议的执行顺序

1. **先补齐 V2 相对 V1 的差距清单**（本文第二节）——已完成
2. **写本地 TTS 模块的接口设计**（不绑定具体引擎）
3. **确认 CosyVoice 在你本机能否安装**（需要你在终端试，沙箱限制我这边装不了）
4. 根据第 3 步结果决定：接入 CosyVoice，或先用轻量引擎跑通链路

---

## 七、需要你确认的问题

1. **CosyVoice 是否必须？** 如果目标是"能听到角色声音"，轻量本地 TTS 更快见效；如果要"音色复刻"，CosyVoice/GPT-SoVITS 才必要。
2. **WSL2 能用吗？** 我这边访问被拒，需要你确认。
3. **Python 3.10 环境是否允许新建？** 本机有 `D:\python\python.exe`（3.10.9）。
4. **是否接受先用轻量引擎打通链路，再换 CosyVoice？**

---

## 八、当前已有资产盘点

| 模块 | 状态 | 符合 V2？ |
| --- | --- | --- |
| `server/llm/` 云端 LLM | ✅ 已验证（DeepSeek flash） | ✅ 符合第四节 |
| `server/dialogue/` 角色卡+会话 | ✅ 已验证 | ✅ 符合第七节 |
| `asr/` 本地 ASR | ⚠️ 代码完成，模型加载被沙箱阻断未验证 | ✅ 符合第三节 |
| TTS | ❌ 未实现 | 需新增 |
| WebSocket | ❌ 未实现 | 阶段四 |
| 固件 | ❌ 未实现 | 阶段五 |

> ASR 的未验证项：模型加载因沙箱无法写 `C:\Users\user\.modelscope` 而失败，
> 需在你本机终端执行验证。详见 `docs/asr-plan.md`。
