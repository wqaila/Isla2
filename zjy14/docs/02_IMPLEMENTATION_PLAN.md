# zjy14 落地方案

> 基于 2026-11-09 对本机环境的实际审计，以及对 CosyVoice3 上游源码的阅读和 4 组真实推理实验。
> 所有"实测"数据都来自本机执行；"估算"和"未验证"项已明确标注。
>
> 对照文档：[Fun-CosyVoice3.0_RTX5070_12GB_完整优化开发提示词.md](../Fun-CosyVoice3.0_RTX5070_12GB_完整优化开发提示词.md)
>
> **2026-11-09 更新**：第十节的 5 个待确认问题已全部确认，决策见 [03_DECISIONS.md](03_DECISIONS.md)。
> 本文第五、七、八、九节已按决策同步修订。

---

## 一、结论摘要（先看这里）

原提示词设定的 12 个目标里，有 **3 个和本机实测结论冲突**，必须先修正再开工，否则方案从第一天就建在错误假设上：

| # | 原提示词假设 | 实测结论 | 处置 |
|:-:|---|---|---|
| 1 | `precision: fp16` 是首选优化项 | **fp16 更慢 7.1%、显存 +27%** | 弃用，固定 fp32 |
| 2 | TTFA P50 ≤ 500ms | 实测首包 **1.17~1.74s**，差 2~3 倍 | 目标改为 ≤1.8s，并说明原因 |
| 3 | 情绪靠 `inference_instruct2` 传入即可 | **带缓存音色时指令被静默丢弃**，且音频拉长 3.5 倍 | 必须走自研混合调用 |

第 3 条是本次审计最重要的发现，直接决定"多情绪表达"这个核心目标能否实现。详见第三节。

**另外**：zjy14 不是从零开始。zjy13 已经跑通 CosyVoice3（WSL2 + RTX 5070），并且有经过烧录验证的 ESP32-S3 固件、WebSocket 协议、角色卡和对话链路。**zjy14 应该做增量升级，不是新建一个平行项目。**

---

## 二、已核实事实

### 2.1 硬件与环境（实测）

| 项 | 值 | 来源 |
|---|---|---|
| GPU | NVIDIA GeForce RTX 5070，12227 MiB | `nvidia-smi` |
| 驱动 | 610.62 | `nvidia-smi` |
| 计算能力 | sm_120 (12, 0) | `torch.cuda.get_device_capability` |
| 空闲显存 | 11017 MiB（桌面已占 2139 MiB） | `torch.cuda.mem_get_info` |
| 运行环境 | WSL2 Ubuntu 24.04.5，内核 6.18.33.2 | `/etc/os-release` |
| Python | 3.10.22 | `~/cosyvoice-venv` |
| PyTorch | 2.8.0+cu128 | venv |
| CUDA Runtime | 12.8 | venv |
| 内存 / CPU | 15 GB / 20 核 | `free -g`、`nproc` |

> ⚠️ **Windows 侧无 PyTorch**。根目录 `venv` 和 `zjy13/.venv` 都没有装 torch。
> CosyVoice 只能在 WSL2 里跑，这与 zjy13 的既有架构一致。

### 2.2 上游仓库与模型（实测）

| 项 | 值 |
|---|---|
| 仓库 | `~/CosyVoice`，origin `FunAudioLLM/CosyVoice` |
| 提交 | `074ca6dc` (2026-05-26) |
| 工作区状态 | `requirements.txt` 已修改，另有 `.bak` / `_filtered.txt` 未跟踪 |
| 模型 | `~/models/Fun-CosyVoice3-0.5B-2512`，9.1 GB |
| 采样率 | 24000 Hz（`cosyvoice3.yaml:8`） |
| token 帧率 | 25（`cosyvoice3.yaml:13`） |
| 权重文件 | `llm.pt` 2.0G、`llm.rl.pt` 2.0G、`flow.pt` 1.3G、`hift.pt` 83M、`campplus.onnx` 28M、`speech_tokenizer_v3.onnx` 970M |

### 2.3 可用推理接口（读源码确认）

`cosyvoice/cli/cosyvoice.py` 里 `CosyVoice3` 继承 `CosyVoice2`，实际可调用：

| 方法 | 签名要点 | 备注 |
|---|---|---|
| `add_zero_shot_spk` | `(prompt_text, prompt_wav, spk_id)` | 缓存参考音频编码 |
| `inference_zero_shot` | `(tts_text, prompt_text, prompt_wav, zero_shot_spk_id='', stream, speed)` | 当前主用 |
| `inference_instruct2` | `(tts_text, instruct_text, prompt_wav, zero_shot_spk_id='', stream, speed)` | **有坑，见第三节** |
| `inference_cross_lingual` | `(tts_text, prompt_wav, zero_shot_spk_id='', ...)` | 跨语种 |
| `inference_instruct` | — | 源码断言仅 `CosyVoice` 类可用，**CosyVoice3 不支持** |
| `inference_vc` | — | 音色转换 |

**官方支持的指令清单**（`cosyvoice/utils/common.py:28` `instruct_list`，共 26 条）：

- 方言 17 种：粤/东北/甘肃/贵州/河南/湖北/湖南/江西/闽南/宁夏/山西/陕西/山东/上海/四川/天津/云南
- 音量 2 条：`as loudly as possible` / `in a very soft voice`
- 语速 2 条：`请用尽可能慢地语速说一句话` / `请用尽可能快地语速说一句话`
- **情绪 3 条：`请非常开心地说一句话` / `请非常伤心地说一句话` / `请非常生气地说一句话`**
- 风格 2 条：小猪佩奇风格 / 机器人方式

**特殊 token**（`cosyvoice/tokenizer/tokenizer.py:150`）：`EMOTION = {HAPPY, SAD, ANGRY, NEUTRAL}`，另外 tokenizer 注册了 `[breath]` `[laughter]` `[sigh]` `[cough]` `[lipsmack]` `<strong>` 等非语言标记。

> ⚠️ `EMOTION` 字典只出现在 ASR 侧的 tiktoken 特殊 token 表里，**官方 TTS 示例从未在合成文本中使用 `<|HAPPY|>`**。我实测过，见第三节 C 组。

---

## 三、关键发现：情绪指令在本模型上是坏的

这是整个 zjy14 的核心技术风险，必须先讲清楚。

### 3.1 现象：指令被静默丢弃

用官方路径 `inference_instruct2`，带缓存音色，同一句文本、不同情绪指令：

| 指令 | 音频时长 | 耗时 | RTF | RMS |
|---|---|---|---|---|
| 基线（无指令） | 3.80s | 2.67s | 0.703 | 0.0923 |
| 请非常开心地说 | **13.64s** | 7.75s | 0.568 | 0.1524 |
| 请非常伤心地说 | **13.76s** | 7.41s | 0.539 | 0.1300 |

**两个完全相反的情绪指令，产出几乎相同的超长音频。** 而且长度是基线的 3.5 倍——这不是"表达了情绪"，是"模型跑飞了"。

### 3.2 根因（读源码定位）

`cosyvoice/cli/frontend.py:168` `frontend_zero_shot()`：

```python
if zero_shot_spk_id == '':
    # 走 prompt_text，逐次编码参考音频
    ...
else:
    model_input = {**self.spk2info[zero_shot_spk_id]}   # ← 直接返回缓存
```

当 `zero_shot_spk_id` 非空时，函数**直接返回缓存字典，完全丢弃传入的 `prompt_text` 参数**。而 `frontend_instruct2()` 正是把 `instruct_text` 当 `prompt_text` 传进去的：

```python
def frontend_instruct2(self, tts_text, instruct_text, prompt_wav, resample_rate, zero_shot_spk_id):
    model_input = self.frontend_zero_shot(tts_text, instruct_text, prompt_wav, resample_rate, zero_shot_spk_id)
    del model_input['llm_prompt_speech_token']      # ← 又删掉了语音 token
    del model_input['llm_prompt_speech_token_len']
    return model_input
```

结果：LLM 端拿到的是**缓存的参考文本**（不是指令），却**没有**对应的语音 token 前缀。文本与语音 token 错位 → 自回归生成失控 → 音频拉长。

### 3.3 这也解释了 zjy13 的一个历史 bug

zjy13 的 `scripts/tts_server_wsl.py:39-46` 有一段注释：

> 实测教训：只写纯文本（不加 `<|endofprompt|>` 前缀）时，注册能成功、健康检查也正常，但每次合成都在卷积层报 `Calculated padded input size per channel: (3). Kernel size: (4)`。原因是 LLM 前缀错位后生成阶段拿到过短的 token 序列。

同一个"前缀错位"家族的问题。我在本次实验中复现了这个报错（F1 变体，见 3.4）。

### 3.4 解法：混合调用模式（已验证可行）

绕过公开 API，手工构造 `model_input`：**保留缓存的音色条件，把 `prompt_text` 换成指令文本，同时删除 `llm_prompt_speech_token`**（对齐官方 `instruct2` 的语义）。

```python
cached = cosy.frontend.spk2info["elysia"]
ttk, ttl = cosy.frontend._extract_text_token(TEXT)
itk, itl = cosy.frontend._extract_text_token(instruct_text)
model_input = {
    "text": ttk, "text_len": ttl,
    "prompt_text": itk, "prompt_text_len": itl,          # ← 指令，不是参考文本
    "flow_prompt_speech_token": cached["flow_prompt_speech_token"],
    "flow_prompt_speech_token_len": cached["flow_prompt_speech_token_len"],
    "prompt_speech_feat": cached["prompt_speech_feat"],
    "prompt_speech_feat_len": cached["prompt_speech_feat_len"],
    "llm_embedding": cached["llm_embedding"],
    "flow_embedding": cached["flow_embedding"],
}
chunks = list(cosy.model.tts(**model_input, stream=False, speed=1.0))
```

**实测结果**：

| 变体 | 结果 |
|---|---|
| F1：保留 `llm_prompt_speech_token` | ❌ 全部报 `Kernel size can't be greater than actual input size` |
| **F2：删除 `llm_prompt_speech_token`** | ✅ **成功，音频长度合理** |

F2 实测数据：

| 标签 | 音频 | 耗时 | RTF | RMS | 过零率 | 包络std |
|---|---|---|---|---|---|---|
| F0 基线 | 3.24s | 3.92s | 1.209 | 0.1083 | 0.1862 | 0.0461 |
| F2 开心 | 4.36s | 2.80s | 0.642 | 0.1415 | 0.1674 | 0.0560 |
| F2 伤心 | 5.28s | 3.04s | 0.576 | 0.1093 | 0.1894 | 0.0437 |

**关键**：F2 模式**保留了缓存的音色条件**，所以既有情绪控制，又不用每次重编码参考音频。这解决了 3.1 的死结。

### 3.5 已导出的盲听样本

`_audio_probe/` 下有 10 个 WAV，请你亲耳判断情绪是否真的生效：

| 文件 | 内容 | 时长 |
|---|---|---|
| `00_baseline.wav` | 基线，无指令 | 3.24s |
| `01_happy.wav` | 请非常开心地说 | 4.12s |
| `02_sad.wav` | 请非常伤心地说 | 4.76s |
| `03_angry.wav` | 请非常生气地说 | 3.40s |
| `04_slow.wav` | 尽可能慢 | 6.76s |
| `05_fast.wav` | 尽可能快 | 2.16s |
| `06_soft.wav` | very soft voice | 5.28s |
| `07_laughter.wav` | 文本尾加 `[laughter]` | 4.20s |
| `08_breath.wav` | 文本尾加 `[breath]` | 3.56s |
| `09_sigh.wav` | 文本尾加 `[sigh]` | 3.88s |

**我必须说清楚**：客观指标（时长、RMS、过零率、包络）显示样本之间有差异，但**这不能证明情绪真的被表达了**——时长差异本身就会让所有指标变化。原提示词第十三条明确要求"不能把请求接受了情绪标签当作声音确实表达了情绪"。**这一步只能靠你听。**

`04_slow` (6.76s) vs `05_fast` (2.16s) vs `00_baseline` (3.24s) 这组证据最强，**语速控制基本可以确认生效**。情绪三兄弟需要你判断。

### 3.6 附带发现：`<|HAPPY|>` 不可用

直接往合成文本里塞 `<|HAPPY|>` token（C 组实验）：模型不报错，音频 5.48s / 4.48s，但这是 token 被当普通文本处理的噪声结果，**不是情绪控制**。官方 TTS 示例从未这样用。**结论：不要走这条路。**

---

## 四、与 zjy13 的关系：复用清单

**决策 D2：zjy14 作为 zjy13 的引擎升级**，不是平行项目。部署拓扑：

```text
现状（zjy13）：
  ESP32 固件 → zjy13/server/ws/server.py → zjy13/server/tts/client.py
                                        → WSL HTTP :50000 → tts_server_wsl.py → CosyVoice3

升级后：
  ESP32 固件 → zjy13/server/ws/server.py → zjy14 引擎服务
                                        → WSL HTTP :50001 → custom_voice/inference/engine.py
                                                          → CosyVoice3（混合调用）
```

**zjy13 侧只改一处**：`TTSConfig.base_url` 从 `:50000` 改到 `:50001`。
zjy14 保持 `/synthesize` 与 `/health` 的请求/响应契约，新增字段全部可选，旧调用不受影响。
**回滚**：改回 `:50000` 即恢复原状。

### 4.1 可直接复用（已验证跑通）

| 资产 | 路径 | 状态 |
|---|---|---|
| WebSocket 协议 v1 | `zjy13/server/protocol/messages.py` + `docs/protocol.md` | ✅ 12 字节二进制帧头，固件侧已对齐 |
| WS 服务端 | `zjy13/server/ws/server.py` (30KB) | ✅ ASR→LLM→TTS 全链路 |
| 上行音频处理 | `zjy13/server/ws/audio.py` | ✅ ES7210 四通道 RMNM 拆解 + 重采样 16k |
| 角色卡 | `zjy13/server/characters/elysia.yaml` | ✅ 爱莉希雅完整人设 + `tts_voice_id` 绑定 |
| 角色卡加载 | `zjy13/server/dialogue/character.py` | ✅ |
| 参考音频 | `zjy13/server/characters/voices/elysia/` | ✅ 24kHz 单声道，含参考文本 |
| ESP32 固件 | `zjy13/firmware/main/` | ✅ 已烧录联调；播放侧固定 24kHz |
| 参考音频挑选工具 | `zjy13/tools/prepare_voice_ref.py` | ✅ 含 FunASR 转写 + 质量筛选 |
| TTS HTTP 服务雏形 | `zjy13/scripts/tts_server_wsl.py` | ⚠️ 可作起点，但要重写 |

**固件侧关键约束**（`app_audio.h:15-19`）：ES7210 与 ES8311 共用 I2S 总线，录音 16kHz、播放 24kHz **不能同时开**。这直接决定流式方案必须做半双工切换，不是简单的全双工。

### 4.2 需要新建（zjy14 的价值所在）

| 能力 | zjy13 现状 | zjy14 要做 |
|---|---|---|
| 多情绪 | ❌ 无 | 混合调用 + 指令模板 + 平滑 |
| 自然语言声音指令 | ❌ 无 | 指令解析器 |
| 角色 CRUD | ❌ 硬编码 YAML | SQLite + API |
| 声音档案管理 | ❌ 目录扫描 | 元数据 + 质量报告 + 版本 |
| 流式合成 | ❌ 非流式 | 分块 + 取消（Phase 4） |
| 非语言事件 | ❌ 无 | 能力验证 + 可控注入 |
| Web 管理界面 | ❌ 无 | React + TS 8 页 |
| 性能监控 | ⚠️ 仅日志 | 指标采集 + 仪表盘 |

### 4.3 明确不动

- 上游 `~/CosyVoice` 仓库：**一行不改**。所有扩展走包装层。
- zjy13 的 `firmware/`、`server/protocol/`、`server/dialogue/`、`server/llm/`：保持原样。
- 已烧录固件：不重新烧录。

### 4.4 部署拓扑（D6：单机）

ASR / LLM / TTS 全在 RTX 5070 这一台机器（WSL2 跑模型，Windows 跑 WS 服务与 Web）。

- 无跨机协议设计
- Service 层不假设进程内调用，保留 HTTP 边界，便于将来拆分
- 显存按单机共存算，见 6.3

---

## 五、目标架构

```text
zjy14/
├─ custom_voice/                  # 新增：引擎层（全部是包装，不改上游）
│  ├─ frontend/
│  │  ├─ normalize.py             # 文本清洗、数字/日期/单位规范化
│  │  ├─ segment.py               # 语义分句（复用上游 split_paragraph）
│  │  └─ instruction.py           # 自然语言 → 结构化参数 → 指令模板
│  ├─ speaker/
│  │  ├─ registry.py              # 声音档案 CRUD
│  │  ├─ validate.py              # 参考音频质量检测（静音/削波/多人/混响）
│  │  └─ cache.py                 # 版本化 speaker 条件缓存
│  ├─ expression/
│  │  ├─ emotion.py               # 情绪映射（3 正式 + 2 实验）+ 强度 + 未知回退
│  │  ├─ smooth.py                # 上下文情绪平滑（有限状态机）
│  │  └─ nonverbal.py             # [laughter]/[breath]/[sigh] 注入 + 成功率台账
│  ├─ inference/
│  │  ├─ engine.py                # ★ 混合调用模式（第三节 F2）
│  │  ├─ scheduler.py             # 单例、队列、并发上限、取消、超时
│  │  └─ vram.py                  # 显存监测、安全余量、低显存档位
│  ├─ audio/
│  │  ├─ convert.py               # 重采样 / 声道 / PCM S16LE
│  │  └─ chunker.py               # 40–100ms 分块 + 边界平滑
│  ├─ server/
│  │  ├─ app.py                   # FastAPI
│  │  ├─ routes/                  # characters / speakers / synthesis / system
│  │  └─ ws_bridge.py             # 对接 zjy13 protocol v1
│  └─ schemas/                    # Pydantic 模型
├─ web/                           # 新增：React + TypeScript
├─ configs/
│  ├─ gpu.yaml                    # ★ 已按实测修正（见 6.1）
│  └─ emotions.yaml               # 情绪 → 指令模板映射
├─ data/
│  ├─ custom_voice.db             # SQLite
│  ├─ characters/                 # 角色档案
│  ├─ speakers/                   # 声音档案
│  └─ reference_audio/            # 参考音频（不覆盖原始文件）
├─ docs/
├─ probe/                         # 本次 Phase 0 探针脚本（可复现）
├─ tests/
└─ scripts/
```

**分层原则**：Web → Service → Engine。Web 层不 import 任何 `cosyvoice.*`。

---

## 六、修正后的配置与验收目标

### 6.1 `configs/gpu.yaml`（按实测修正）

```yaml
device: cuda
precision: fp32                  # ★ 实测 fp16 更慢 7.1%、显存 +27%
max_concurrent_requests: 1       # 先单并发稳定
enable_compile: false            # 未测，默认关
enable_sdpa: auto
enable_vllm_llm: false           # 模型目录无 vllm/ 子目录，不适用
speaker_cache_enabled: true      # ★ 实测省 16.5%
speaker_cache_max_items: 8
cpu_offload: false
memory_safety_margin_mb: 1024
token_hop_len_reset: true        # ★ 修复上游跨调用污染
instruct_mode: hybrid_f2         # ★ 混合调用，见 3.4
```

与原文档候选值的差异，逐条都有实测依据，不是随意改。

### 6.2 验收目标修订

| 指标 | 原目标 | 实测值 | 修订目标 | 理由 |
|---|---|---|---|---|
| TTFA P50 | ≤500ms | 1.17~1.74s | **≤1.8s** | 模型能力边界，非工程问题 |
| TTFA P95 | ≤1000ms | ~2.25s（污染时） | **≤2.5s** | 修复 hop_len 后重测 |
| RTF | ≤0.8 | 0.70（fp32 稳态） | **≤0.8** | ✅ 可达 |
| 峰值显存 | — | 3.45~3.57 GB | **≤4.5 GB** | 留足 ASR 共存空间 |
| 30 分钟连续播放 | 无卡顿 | 未测 | 未测 | 需 Phase 7 |
| 显存泄漏 | 无持续增长 | 未测 | 未测 | 需 Phase 7 |
| 授权角色数 | ≥3 | 仅 1（elysia） | **1，注明素材限制** | D9，见 R11 |

**关于 TTFA 必须说明**：首包延迟的构成（zjy13 实测分解）是 LLM 生成 28 token 约 0.86s + Flow 0.5s + HiFT 0.3s。要压到 500ms 只能砍 `token_hop_len`，但那是模型训练时的静态分块大小，砍了会导致音质劣化和重复推理。**这是模型层面的硬约束，工程上绕不过去。**

### 6.3 显存预算（实测）

```
总显存           12227 MiB
桌面已占          2139 MiB
TTS 峰值          3533 MiB  (3.45 GB)
ASR 估算          2048 MiB  (来自 zjy13 docs，未本次实测)
────────────────────────────
剩余             4507 MiB  ≈ 4.4 GB 安全余量
```

12GB 跑 TTS + ASR 是**够的**，这也是本次审计的一个正面结论。

---

## 七、阶段计划

原文档的 7 阶段顺序基本合理，但有两处必须调整：

- **Phase 3（表达控制）提到 Phase 2 之前**：情绪是整个项目的核心卖点，也是最大技术风险（第三节）。先验证它，失败的话整个项目定位要改。
- **Phase 5（ESP32）大幅压缩**：zjy13 已经做完了，zjy14 只需适配，不需要重做。

### Phase 0：审计与基线（本次已完成约 80%）

| 交付物 | 状态 |
|---|---|
| 环境审计（本文件第二节） | ✅ 完成 |
| 上游源码接口清单（2.3） | ✅ 完成 |
| 情绪指令可行性验证（第三节） | ✅ 完成，**有重大发现** |
| 混合调用模式验证（3.4） | ✅ 完成，方案可行 |
| 盲听样本 10 个（3.5） | ✅ 已导出，**待你听** |
| `docs/00_REPOSITORY_AUDIT.md` | ⬜ 待写（内容已在本文） |
| `docs/01_BASELINE_REPORT.md` | ⬜ 待写（含 10 短 + 10 长文本基线） |
| 环境检测脚本 | ✅ `probe/` 下 5 个脚本 |

**剩余工作**：补齐正式基线报告（需跑 20 条文本 × 3 次），约 0.5 天。

### Phase 1：工程骨架（2–3 天）

- `custom_voice/` 目录结构 + `pyproject.toml`
- FastAPI 骨架 + `/health` `/api/v1/system/{status,gpu}`
- SQLite 数据模型（characters / speakers / synthesis_logs / metrics）
- 结构化日志（request_id 贯穿）、异常体系、配置加载与校验
- pytest 骨架

**验收**：`pytest` 全绿；`/health` 返回真实模型状态；配置文件字段全部有代码消费（不允许"配置看似生效但实际未使用"）。

### Phase 2：表达控制（4–5 天）★ 核心风险

**按 D1「两手准备」实现**：接口按完整情绪设计，但"情绪 → 指令"做成可替换策略，
待盲听结论后切换路线，不改调用方代码。

- `expression/emotion.py`：情绪 → 指令模板映射
  - 正式：neutral / happy / sad / angry（官方 3 条指令 + 基线）
  - 实验：comforting / playful（自造指令，配置带 `experimental: true`）
  - 未知标签回退 neutral
- `inference/engine.py`：落地混合调用模式（3.4），封装 `synthesize_with_instruction()`
- `frontend/instruction.py`：自然语言 → 结构化参数（"温柔一点、放慢速度" → `{style: gentle, speed: 0.92}`）
- `expression/smooth.py`：有限状态机，限制单轮情绪跳变幅度（路线乙下用于语速/停顿的连续变化）
- `expression/nonverbal.py`：`[laughter]`/`[breath]`/`[sigh]` 注入 + 成功率台账
- **A/B 听感测试集**：3 正式情绪 + 2 实验情绪 + 基线，× 5 文本 = 30 个样本

**验收（两条路线，按 D1 结论择一）**：

*路线甲（情绪有效）*
- 客观：30 个样本时长/RMS/包络指标有区分度
- 主观：盲听 6 种情绪至少 4 种可辨认

*路线乙（情绪无效，降级）*
- 只保留 speed / volume / pause 三个确定生效维度
- 情绪由 LLM 侧用词表达（复用 zjy13 角色卡 `emotional_expression`）
- 文档如实标注："模型不支持独立情绪控制"

**共同要求**：非语言事件逐项记录成功率，不达标的**明确标注"模型不支持"**。

### Phase 3：角色与声音管理（3–4 天）

- 角色 CRUD API + SQLite 持久化
- 声音档案：元数据、质量报告（静音/削波/时长/采样率/声道）、授权状态
- 参考音频校验：`speaker/validate.py`（复用 `zjy13/tools/prepare_voice_ref.py` 的逻辑）
- 版本化缓存：`(speaker_id, model_version, preprocessing_version)` → 缓存 key
- 迁移：把 `elysia.yaml` 导入为第一个角色

**验收**：创建 → 绑定声音 → 试听 全流程可用；换参考音频后缓存自动失效。

### Phase 4：GPU 与流式优化（3–4 天）

- 落地 `configs/gpu.yaml`，逐项验证生效
- 流式分块：`token_hop_len` 每请求重置（已验证的必要修复）
- 音频块 40–100ms 可配置，测边界平滑（是否有咔哒声）
- 取消延迟测量、并发从 1 逐步加到 2
- 显存监测 + 不足时明确报错
- 性能报告：TTFA / RTF / Peak VRAM / Chunk gap / Cancellation latency

**验收**：RTF ≤0.8 稳定；单并发 30 分钟无显存增长；取消延迟 <200ms。

### Phase 5：ESP32-S3 桥接验证（1–2 天，D7 范围收敛）

**不重做固件**。zjy13 的固件、协议、WS 服务全部复用，本阶段只做桥接：

- `server/ws_bridge.py`：zjy14 引擎 ↔ zjy13 protocol v1
- zjy13 侧唯一改动：`TTSConfig.base_url` 从 `:50000` 改到 `:50001`
- zjy14 HTTP 接口**保持 `/synthesize` 与 `/health` 的请求/响应契约**，旧客户端不受影响
- 24kHz PCM S16LE 单声道输出（固件已固定此格式，无需重采样）
- 背压：缓冲高低水位、丢块检测
- 半双工约束（`app_audio.h:15`）：播放时不能录音，明确记录为固件限制
- 断连重连、旧 request_id 隔离

**验收**：板子能播 zjy14 合成的音频；弱网、断连、取消场景有测试记录。
**回滚**：`base_url` 改回 `:50000` 即恢复原状。

### Phase 6：Web 管理界面（5–7 天）

8 个页面：仪表盘 / 角色 / 声音 / 情绪 / 合成测试 / 设备 / 系统设置 / 日志中心。

- React + TypeScript + Vite
- 中文界面优先
- **情绪页含 6 情绪并排 A/B 对照试听**（D1 的关键决策工具）
- 实验性情绪（comforting / playful）在 UI 上明确标记
- 危险操作二次确认

### Phase 7：稳定性与发布（3–4 天）

- 30 分钟连续播放、冷/热启动、显存泄漏
- 一键启动脚本（Windows + WSL 两段）
- 故障排查手册、配置模板、变更记录、回滚方案
- `docs/ACCEPTANCE_REPORT.md`：只填实测数据，未测标"未测试"

### 工作量汇总

| 阶段 | 估算 | 风险 |
|---|---|---|
| Phase 0 剩余 | 0.5 天 | 低 |
| Phase 1 骨架 | 2–3 天 | 低 |
| **Phase 2 表达控制** | **4–5 天** | **高** |
| Phase 3 角色声音 | 3–4 天 | 中 |
| Phase 4 GPU/流式 | 3–4 天 | 中 |
| Phase 5 ESP32 桥接 | **1–2 天** | 低（复用） |
| Phase 6 Web | 5–7 天 | 中 |
| Phase 7 稳定发布 | 3–4 天 | 低 |
| **合计** | **约 22–29 人日** | |

---

## 八、风险清单

| # | 风险 | 等级 | 影响 | 缓解 |
|:-:|---|:-:|---|---|
| R1 | 情绪表达可能本质不成立 | **高** | 核心目标失效 | Phase 2 前置验证；盲听不过就降级并如实报告 |
| R2 | 混合调用绕过公开 API，上游更新会破坏 | 中 | 维护成本 | 封装在 `engine.py` 单点；锁上游 commit；升级前跑回归 |
| R3 | 丢 `llm_prompt_speech_token` 可能影响音色一致性 | 中 | 角色串音色 | Phase 2 做"基线 vs 混合模式"音色 A/B |
| R4 | TTFA 目标 500ms 无法达成 | 中 | 验收不通过 | 已修订为 1.8s，并说明是模型边界 |
| R5 | WSL2 路径 `/mnt/d/` 跨系统 IO 慢 | 低 | 参考音频加载慢 | 已缓存，实测影响可忽略 |
| R6 | `requirements.txt` 已被改动，环境不可复现 | 中 | 换机重装困难 | 导出 `pip freeze` 并记录补丁 |
| R7 | 12GB 显存跑 TTS+ASR+Web 三方 | 低 | OOM | 实测余量 4.4GB；Web 用 CPU |
| R8 | 半双工 I2S 限制打断体验 | 中 | 无法"边说边听" | 明确记录为固件约束，不做承诺 |
| R9 | 上游 `inference_instruct` 对 CosyVoice3 不可用 | 低 | 少一条路径 | 已知，只用 `instruct2` 语义 |
| R10 | 情绪指令导致音频异常拉长（13.6s） | 高 | 若误用直接跑飞 | 已定位根因，`engine.py` 强制走 F2 |
| R11 | **只有 1 个角色素材，无法验证"跨角色不串音色"** | 中 | 原文档验收项无法执行 | D9 已降级为单角色做深；验收报告注明素材限制 |
| R12 | 自造情绪指令（comforting/playful）可能只改标签不改声音 | 中 | 用户体验落差 | D4 标 `experimental: true`，UI 明确区分，不计入正式验收 |

---

## 九、已确认的决策（2026-11-09）

原第十节的 5 个问题已全部确认，完整记录见 [03_DECISIONS.md](03_DECISIONS.md)。摘要：

| # | 决策 |
|:-:|---|
| D1 | 情绪盲听未做，方案按**两手准备**设计（路线甲多情绪 / 路线乙降级），验收口径待听后锁定 |
| D2 | zjy14 作为 zjy13 的**引擎升级**，复用协议/固件/角色卡 |
| D3 | **就地建设并纳入 git** |
| D4 | **官方 3 条情绪打底 + 自造 2 条标注实验性** |
| D5 | **Phase 2–3 非流式，Phase 4 上流式 + 长短句路由** |
| D6 | **同一台机器**，无跨机协议 |
| D7 | **只做 TTS 引擎 + 管理接口**，ASR/LLM/固件全部复用 |
| D8 | **React + TS 完整 8 页** |
| D9 | **暂时只有 elysia，先做深单角色** |
| D10 | **默认仅 localhost，需要时显式开启 + token** |

因决策产生的修订：

| 原方案 | 修订后 | 依据 |
|---|---|---|
| Phase 5 ESP32 联调 2–3 天 | 桥接验证 **1–2 天** | D7 + D2 |
| 验收要求 3 个授权角色 | 降为 **1 个**，注明素材限制 | D9 |
| 情绪 6 种全部正式验收 | **3 正式 + 2 实验性** | D4 |
| Phase 2 直接实现完整情绪 | **策略可替换**，等 D1 结论 | D1 |
| 主机 A/B 分离架构 | **单机** | D6 |
| 合计 23–31 人日 | **约 22–29 人日** | 上述合计 |

---

## 十、下一步

决策已全部确认（见 [03_DECISIONS.md](03_DECISIONS.md)），可以进 Phase 1。

**待你补充**（不阻塞 Phase 1）：
1. `_audio_probe/` 盲听结论 —— Phase 2 开始前必须给出，决定走路线甲还是乙
2. 是否还有 elysia 之外的角色素材 —— Phase 3 前
3. 情绪模板的听感偏好（哪些"安慰"语气更像角色）—— Phase 2 中期

**我建议的开工顺序**：Phase 1 骨架（2–3 天）→ Phase 2 表达控制（4–5 天，等盲听结论）。
骨架不依赖情绪结论，可以先做。

---

## 附：本次探针产物

| 文件 | 用途 |
|---|---|
| `probe/_probe_env.sh` | 环境与依赖探测 |
| `probe/_probe_api.sh` / `_probe_api2.sh` | 上游 CLI 接口清单 |
| `probe/_probe_frontend.sh` | frontend 内部实现（定位 bug 用） |
| `probe/_probe_emotion.sh` | EMOTION / 特殊 token 定义 |
| `probe/_probe_common.sh` | `instruct_list` 官方指令清单 |
| `probe/_probe_emotion_control.py` | 实验 A/B/C/D：情绪与指令可行性 |
| `probe/_probe_instruct_path.py` | 实验 E：证实缓存丢弃指令 |
| `probe/_probe_hybrid.py` | 实验 F：混合调用模式验证 |
| `probe/_probe_audio_samples.py` | 导出盲听样本 |
| `_audio_probe/*.wav` | 10 个盲听样本 |

复现命令：

```powershell
wsl -e bash /mnt/d/xiangmudaima/zjy14/probe/_probe_env.sh
wsl -e bash -lc "source ~/cosyvoice-venv/bin/activate && cd ~/CosyVoice && python /mnt/d/xiangmudaima/zjy14/probe/_probe_hybrid.py"
```
