# zjy13 开发全记录

> 项目：ESP32-S3 智能角色语音交互系统
> 架构：**本地 ASR + 云端 LLM + 本地 TTS**（V2 方案）
> 记录时间：2026-10-26
> 所有数据均为真实执行结果，未使用模拟数据。

---

## 一、项目概览

### 目标

基于 ESP32-S3-Touch-LCD-1.85B 开发板与 Windows 电脑，做一套智能角色语音交互系统：

- **ASR**：本地 FunASR Paraformer，RTX 5070 加速
- **LLM**：云端 DeepSeek API
- **TTS**：本地 CosyVoice 3.0，跑在 WSL2

### 当前进度

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| — | 云端 LLM 模块 | ✅ 完成并验证 |
| 一 | 本地 ASR 验证 | ✅ 完成并验证 |
| 二 | 云端 LLM 验证 | ✅ 完成并验证 |
| 三 | 本地 TTS + 终端闭环 | ✅ 完成并验证 |
| 四 | WebSocket 通信 | ⬜ 未开始 |
| 五 | ESP32 固件 | ⬜ 未开始 |
| 六 | 整机联调 | ⬜ 未开始 |

---

## 二、环境

| 项 | 值 |
| --- | --- |
| 操作系统 | Windows 10.0.26100 |
| Python（LLM 侧） | 3.11.9，`zjy13/.venv` |
| Python（ASR 侧） | 3.11.9，`zjy13/.venv-asr` |
| Python（TTS 侧） | 3.10.22，WSL2 内 `~/cosyvoice-venv` |
| GPU | NVIDIA GeForce RTX 5070，12227 MiB |
| **计算能力** | **sm_120（Blackwell）** |
| 驱动 | 610.62（Windows）/ 610.43.02（WSL） |
| CUDA | 12.8 |
| PyTorch | 2.8.0+cu128（三处环境统一） |
| WSL | 2.7.14.0，Ubuntu 24.04 |

### 关键约束：sm_120

RTX 5070 是 Blackwell 架构，**必须用 CUDA 12.8+ 构建的 PyTorch ≥ 2.7.0**。

实测证据（旧版 torch 2.1.0+cu118）：

```
NVIDIA GeForce RTX 5070 with CUDA capability sm_120 is not compatible
with the current PyTorch installation.
arch_list: sm_37 ... sm_90    ← 不含 sm_120
```

**陷阱**：此时 `torch.cuda.is_available()` 仍返回 `True`，简单矩阵乘法也能算出结果
（走 PTX JIT），极易误判为"GPU 正常"。实际跑模型会算子缺失或极慢。

因此代码里额外校验 `get_arch_list()` 是否包含当前算力架构。

---

## 三、模块清单

### 3.1 云端 LLM（`server/llm/`）

| 文件 | 职责 |
| --- | --- |
| `base.py` | ChatMessage / LLMResult / 10 类错误体系 |
| `adapter.py` | ChatAdapter 注册表 + OpenAI 兼容实现 |
| `client.py` | LLMClient：流式/重试/取消/turn 去重/计时 |

**能力**：

- 供应商无关（适配器模式）
- 流式输出 + 首 token 计时
- 有限重试 + 指数退避
- turn 级去重（防重复扣费）
- 取消当前轮次
- 错误分类（鉴权/限流/超时/网络/协议）
- 日志不含密钥

**实测**（DeepSeek `deepseek-flash`，thinking=disabled）：

| 指标 | 值 |
| --- | --- |
| 首字延迟 mean | 646 ms |
| 首字延迟 P95 | 984 ms |
| 总耗时 mean | 996 ms |
| prompt 缓存命中 | 768/1012 token |

### 3.2 角色卡与会话（`server/dialogue/`）

| 文件 | 职责 |
| --- | --- |
| `character.py` | 角色卡加载 + 结构化提示词组装 + few-shot |
| `session.py` | 多轮历史 / 轮次管理 / 取消 / 软截断 |
| `guards.py` | 空历史 + 记忆类提问的结构性拦截 |
| `text_utils.py` | 句边界软截断、可朗读化 |

**角色卡 v1.3**，7 组示例对话，系统提示词 1351 字。

### 3.3 本地 ASR（`asr/`）

| 文件 | 职责 |
| --- | --- |
| `audio_utils.py` | 格式探测/重采样/声道降混/规范化/异常校验 |
| `engine.py` | FunASR 封装：加载一次、预热、GPU 架构校验 |
| `transcribe.py` | CLI 入口 |

**实测**（Paraformer-large，5.55s 音频）：

```
识别: 欢迎大家来体验达摩院推出的语音识别模型
推理 49.7 ms | RTF 0.009 | 模型加载 1695 ms | 预热 456 ms
```

RTF 0.009 = 比实时快 111 倍，**ASR 不是瓶颈**。

### 3.4 本地 TTS（`server/tts/` + WSL 服务）

| 文件 | 位置 | 职责 |
| --- | --- | --- |
| `server/tts/client.py` | Windows | HTTP 客户端，引擎无关接口 |
| `scripts/tts_server_wsl.py` | WSL2 | FastAPI 服务，封装 CosyVoice3 |

**实测**（CosyVoice3-0.5B，3.3s 音频）：

| 指标 | 值 |
| --- | --- |
| 模型加载 | 9.2 s |
| 预热 | 3.4 s |
| 合成耗时 | 2.1 s |
| RTF | 0.62 ~ 0.70 |
| 峰值显存 | 3.57 GB |

### 3.5 终端闭环（`tools/dialogue_loop.py`）

ASR → LLM → TTS 完整链路，分段计时。

**实测**：

| 阶段 | 耗时 | 占比 |
| --- | --- | --- |
| ASR | 106 ms | 2.5% |
| LLM | 1142 ms | 27.0% |
| TTS | 2981 ms | 70.5% |
| **端到端** | **4228 ms** | |

---

## 四、开发过程中发现并修复的问题

### 4.1 角色卡：口头禅被当成"不知道"的固定开头

**现象**：连续 4 次测试，回答记忆类问题时全部以「这个我得确认一下」起手。

**根因**：模板里【口头禅】和【不确定时的处理】相邻，且该口头禅语义就是"不确定"，
模型把两者绑成了条件反射。

**修复**：新增 `catchphrase_usage` 字段，明确"只是偶尔点缀，不是固定开头"。

### 4.2 角色卡：把"没有长期记忆"误解成"看不见对话历史"

**现象**：多轮对话中问"你还记得我在做什么"，模型答"不记得，我只能看到这次对话"。

**排查**：打印实际 payload，确认历史**完整传入**，所以不是代码 bug。

**根因**：角色卡从未告诉模型"本次会话上下文可见"。

**修复**：模板新增【本次对话的上下文】段落。验证 4/4 正确回答。

### 4.3 角色卡：空历史时编造用户经历（严重）

**现象**：全新会话问"你还记得我刚才在做什么吗"，模型编造
"你刚才在整理一份表格"。

**尝试**：先加提示词"禁止虚构"，3 次里仍有 1 次编造。

**最终修复**：结构性拦截（`guards.py`）。空历史 + 记忆类提问时，
直接返回确定回答，不交给模型。验证 3/3 稳定。

### 4.4 会话：重置后 turn_id 重复

**现象**：点"清空对话"后再提问，报 `duplicate_turn`。

**根因**：`reset()` 只清历史没换 session_id，下一轮 turn_id 撞车。

**修复**：重置时重新生成 session_id。

### 4.5 TTS：token_hop_len 跨调用污染（上游 bug）

**代码**（`cosyvoice/cli/model.py:360`）：

```python
self.token_hop_len = min(self.token_max_hop_len, self.token_hop_len * self.stream_scale_factor)
```

`token_hop_len` 是实例属性，出包后翻倍（25→50→100），**合成结束不重置**。

**实测证据**：

```
第1次: 进入时 hop= 25 | 首包 1.75s | 包数 3 | 退出时 hop=100
第2次: 进入时 hop=100 | 首包 2.25s | 包数 1 | 退出时 hop=100   ← 污染
```

**影响**：首包慢 29%，流式退化成一整包，**静默失效**。

**修复**：每次合成前重置 `token_hop_len = 25`，首包稳定 1.74s。

### 4.6 TTS：fp16 实测无效

| 指标 | fp32 | fp16 | 变化 |
| --- | --- | --- | --- |
| 合成耗时 | **2.541 s** | 2.721 s | +7.1%（慢） |
| 峰值显存 | **3.57 GB** | 4.53 GB | +27% |

**结论**：不采用 fp16。原因：LLM 逐 token 自回归，瓶颈在 kernel 调度不在算力。

### 4.7 环境：ModelScope 缓存路径不可写

**现象**：`[WinError 5] 拒绝访问。: 'C:\Users\user\.modelscope'`

**根因**：FunASR 的 `snapshot_download()` 没传 `cache_dir`，用默认路径；
而该机器 `C:\Users\user` 下**任何新目录都无法创建**。

**解决**：写 `tools/fetch_model.py` 直接下载到 `_models/`，
把本地路径传给 `AutoModel`。

### 4.8 环境：缺少音频解码后端

**现象**：`Couldn't find appropriate backend` / `cannot load library 'libsndfile.dll'`

**根因**：FunASR 读文件依赖 soundfile 或 ffmpeg，本机两者都缺。

**解决**：改用 numpy float32 数组传参给 `generate()`，
用自写的 `read_pcm()`（纯标准库 wave + numpy）读取。

**注意**：必须传 float32，传 int16 会报 `"normal_kernel_cpu" not implemented for 'Short'`。

### 4.9 环境：pip 的 TLS 被拦截

**现象**：`SSL: UNEXPECTED_EOF_WHILE_READING`，但同机 httpx 访问相同 URL 正常。

**解决**：写 `tools/pip_fetch.py`，用 httpx 解析 PyPI JSON 下载 wheel 再本地安装。

### 4.10 环境：大文件下载极慢

| 源 | 速度 |
| --- | --- |
| 阿里云 pytorch 镜像 | 0.03 MB/s（不可用） |
| download.pytorch.org 单连接 | 0.15 MB/s |
| **download-r2.pytorch.org 分块并发** | **3.6 MB/s** |

**解决**：写 `tools/fetch_wheel.py`，Range 分块 + 24 并发。
3461 MB 的 torch 用 17 分钟下完。

---

## 五、测试覆盖

| 环境 | 测试数 | 覆盖内容 |
| --- | --- | --- |
| `.venv`（LLM+TTS） | **123** | 配置/适配器/客户端/角色卡/会话/守卫/文本处理/TTS 客户端 |
| `.venv-asr`（ASR） | **28** | 音频格式/重采样/规范化/引擎/架构校验 |

关键测试：

- `test_sm120_missing_raises`：架构不匹配必须报错
- `test_service_unavailable_raises`：TTS 不可达不得静默降级
- `test_guard_triggers_on_empty_history`：空历史拦截编造
- `test_duplicate_turn_rejected`：防重复扣费
- `test_time_context_clarifies_source`：时间来源必须说清

---

## 六、工具脚本

| 脚本 | 用途 |
| --- | --- |
| `tools/fetch_wheel.py` | 分块并发下载大 wheel |
| `tools/fetch_model.py` | 从 ModelScope 直接下载模型 |
| `tools/pip_fetch.py` | 绕过 pip TLS 安装依赖 |
| `tools/llm_smoke.py` | LLM 冒烟测试 |
| `tools/bench_models.py` | 模型对比基准 |
| `tools/chat_demo.py` | 终端对话演示 |
| `tools/web_demo.py` | 网页对话演示 |
| `tools/demo_report.py` | 生成 HTML 报告 |
| `tools/dialogue_loop.py` | **完整语音闭环** |

WSL 侧脚本：

| 脚本 | 用途 |
| --- | --- |
| `scripts/setup_cosyvoice_wsl.sh` | CosyVoice 安装 |
| `scripts/repair_cosyvoice_wsl.sh` | 依赖修复 |
| `scripts/test_cosyvoice_wsl.sh` | 合成测试 |
| `scripts/bench_cosyvoice_wsl.sh` | 性能基线 |
| `scripts/compare_fp16_v2.sh` | 精度对比 |
| `scripts/test_spk_cache_wsl.sh` | 缓存效果 |
| `scripts/verify_hop_len_bug.sh` | bug 验证 |
| `scripts/test_stream_wsl.sh` | 流式首包 |
| `scripts/profile_tts_stages.sh` | 三段剖析 |
| `scripts/serve_tts_wsl.sh` | **启动 TTS 服务** |

---

## 七、文档清单

| 文档 | 内容 |
| --- | --- |
| `docs/asr-plan.md` | ASR 方案与版本选型依据 |
| `docs/asr-report.md` | ASR 验收报告（含 5 个坑） |
| `docs/tts-report.md` | TTS 首次合成验收 |
| `docs/tts-performance.md` | TTS 性能优化实测 |
| `docs/v2-direction.md` | V2 方案转向分析 |
| `docs/wsl2-cosyvoice-guide.md` | WSL2 部署指南 |
| `docs/wsl2-gpu-verified.md` | WSL2 GPU 验证结果 |
| `docs/character-review.md` | 角色卡审查（3 个缺陷） |
| `docs/model-selection.md` | DeepSeek 模型选型 |
| `docs/demo-guide.md` | 演示操作指南 |
| `docs/stage3-dialogue-loop.md` | 阶段三验收 |
| `docs/development-log.md` | **本文件** |

---

## 八、关键决策记录

| 决策 | 理由 |
| --- | --- |
| 用 deepseek-flash 而非 v4-pro | 首字延迟低 230ms，角色表现更自然 |
| 关闭 thinking | 首字延迟降低约 1 秒 |
| 不做双模型路由 | 第一版以简单为先 |
| TTS 用 CosyVoice3 而非 300M | 音质优先，且支持 instruct 风格控制 |
| TTS 跑在 WSL2 | 官方只支持 Linux，Windows 原生依赖 sox 等难装 |
| 不用 fp16 | 实测慢 7.1%，显存 +27% |
| 第一版用非流式 TTS | 流式只快 0.4 秒，复杂度不值 |
| 参考音频缓存 | 免费省 16.5% |

---

## 九、未验证项（如实标注）

### 硬件相关（全部未验证）

- ESP32-S3-Touch-LCD-1.85B 的实际引脚定义
- 麦克风采集格式（采样率/位宽/声道）
- 扬声器播放能力与格式要求
- LCD 驱动与显示

> 按 V2 第二节要求，**不得凭空编造引脚号或假定音频驱动可用**。
> 这些需要查官方资料或实际测试。

### 软件相关

- 真实麦克风录音（只用过官方示例 WAV）
- 长音频（> 1 分钟）
- 噪声环境
- ASR + TTS 同时驻留显存的并发表现
- 连续 10 轮以上对话的稳定性
- 流式音频拼接处的音质
- 打断/取消机制

### 音质相关

- **CosyVoice3 合成音频的主观听感**（需人工试听）
- 音色是否像"小满"（当前用官方示例音频，不是角色专属）

---

## 十、当前已知问题

| 问题 | 影响 | 状态 |
| --- | --- | --- |
| 端到端 4.2 秒 | 用户会明显感到等待 | 可接受，有优化空间 |
| TTS 占 70% 延迟 | 瓶颈明确 | 流式可降 1.2 秒 |
| 音色不是角色专属 | 角色感弱 | 需录参考音频 |
| 文本正则化用了回退方案 | 中文数字读法可能不完美 | 需装 wetext 或 ttsfrd |

---

## 十一、复现步骤

### 1. 启动 TTS 服务（WSL2）

```powershell
wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/serve_tts_wsl.sh
```

### 2. 跑完整闭环

```powershell
cd D:\xiangmudaima\zjy13
$env:PYTHONUTF8="1"
.\.venv-asr\Scripts\python.exe -m tools.dialogue_loop "tests\audio\asr_example.wav"
```

### 3. 文本模式（跳过 ASR）

```powershell
.\.venv\Scripts\python.exe -m tools.dialogue_loop --text-mode
```

### 4. 跑测试

```powershell
# LLM + TTS
.\.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_audio_utils.py --ignore=tests/test_asr_engine.py

# ASR
.\.venv-asr\Scripts\python.exe -m pytest tests/test_audio_utils.py tests/test_asr_engine.py -q
```

---

## 十二、下一步

### 阶段四：WebSocket 通信

按 V2 第十一节：

1. 定义统一消息协议（17 种消息类型）
2. 实现客户端模拟器
3. 测试音频上传、文字返回、音频下载
4. 测试重复消息、超时、取消、连接中断

**前置条件**：需要先确定开发板音频采集格式。

### 待决策

- 音色：现在用官方示例音频，是否要录小满专属声音
- 是否先做协议设计（不依赖硬件细节）
