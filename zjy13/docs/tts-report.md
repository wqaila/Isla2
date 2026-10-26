# 本地 TTS 验收报告（CosyVoice 3.0）

> 状态：**合成成功**，但性能未达预期，需进一步确认。
> 所有数据均来自 WSL2 真实执行，未使用模拟数据。

---

## 一、环境

| 项 | 值 |
| --- | --- |
| 运行环境 | WSL2 + Ubuntu 24.04 |
| Python | 3.10.22（CosyVoice 官方要求 3.10） |
| PyTorch | 2.8.0+cu128 |
| GPU | NVIDIA GeForce RTX 5070（WSL 透传） |
| 计算能力 | sm_120，架构匹配 True |
| 模型 | Fun-CosyVoice3-0.5B-2512（约 3.8 GB） |
| 采样率 | 24000 Hz |

### 安装过程中的关键决策

**官方 requirements.txt 锁 `torch==2.3.1`，不支持 sm_120。**

处理方式：先单独装 `torch==2.8.0+cu128`，再从 requirements 中删除 torch/torchaudio 行，
防止 pip 把版本降回去。同时过滤了 `deepspeed`、`tensorrt-cu12*`（训练/Linux 专属，推理不需要）。

**踩坑记录**：

1. `openai-whisper` 构建失败 —— `setuptools 80+` 移除了 `pkg_resources`。
   修复：装 `setuptools<80`。
2. **我一开始过滤掉了 `openai-whisper`，判断"推理不需要"是错的。**
   `cosyvoice/cli/frontend.py:20` 有 `import whisper`，属于硬依赖。
   教训：判断依赖是否必需应查代码 import，不能凭功能推断。
3. `onnxruntime` 缺失 —— 过滤 `onnxruntime-gpu` 时连带没了 CPU 版。
   修复：单独装 `onnxruntime`（CPU 版足够，`campplus.onnx` 只有 27 MB）。

---

## 二、合成结果（真实）

三个 WAV 均成功生成，格式统一：

| 文件 | 采样率 | 位深 | 声道 | 时长 | 大小 |
| --- | --- | --- | --- | --- | --- |
| `cosyvoice_zero_shot.wav` | 24000 Hz | 16 bit | 1 | 3.76 s | 176 KB |
| `cosyvoice_zero_shot2.wav` | 24000 Hz | 16 bit | 1 | 2.64 s | 124 KB |
| `cosyvoice_instruct.wav` | 24000 Hz | 16 bit | 1 | 2.28 s | 107 KB |

内容：

| 文本 | 模式 |
| --- | --- |
| 今天天气不错，我们下午去公园散步吧。 | 零样本克隆 |
| 你好，我是小满。有什么想聊的吗？ | 零样本克隆 |
| 别急，慢慢来就好。（温柔缓慢） | instruct2 风格控制 |

> ⚠️ **音频听感未验证**：我无法播放音频，音质、音色相似度、自然度需要你实际听。

---

## 三、性能数据（存在问题）

| 测试 | 音频时长 | 合成耗时 | RTF |
| --- | --- | --- | --- |
| zero_shot（第 1 次） | 3.76 s | 4.39 s | **1.168** |
| zero_shot2（第 2 次） | 2.64 s | 1.96 s | 0.743 |
| instruct（第 3 次） | 2.28 s | 1.87 s | 0.822 |

### ⚠️ 问题：首次 RTF 大于 1

第 1 次合成 RTF = 1.168，意味着**合成比实时还慢**。第 2、3 次降到 0.74 / 0.82。

两种可能：

1. **冷启动**：CUDA kernel 首次编译、显存分配、缓存未就绪
2. **文本长度效应**：第一句 18 字，后两句 16 / 8 字，可能有非线性开销

**必须测清楚**，否则无法判断 TTS 是否满足实时交互要求。
`scripts/bench_cosyvoice_wsl.sh` 就是为此写的：先预热一次，再连续 5 次同一文本，
观察 RTF 是否稳定。

### 与 ASR 的对比

| 模块 | RTF | 说明 |
| --- | --- | --- |
| 本地 ASR | **0.009** | 比实时快 111 倍，完全不是瓶颈 |
| 本地 TTS | 0.74 ~ 1.17 | **是潜在瓶颈** |

ASR 5.5 秒音频只需 50 毫秒，而 TTS 合成 3.76 秒音频要 4.39 秒。
**端到端延迟将主要由 TTS 决定。**

---

## 四、API 使用要点（实测确认）

### CosyVoice3 没有预置音色

```python
cosy.list_available_spks()   # 返回 [] 空列表
cosy.inference_sft(text, '中文女')   # KeyError: '中文女'
```

**必须用零样本克隆**：

```python
cosy.inference_zero_shot(
    text,
    'You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。',
    './asset/zero_shot_prompt.wav',
    stream=False,
)
```

### prompt 文本格式

```
You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。
└────── 指令部分 ──────┘  └─分隔符─┘ └──── 参考音频对应的文字 ────┘
```

`<|endofprompt|>` 分隔符必须存在。

### 警告信息说明

| 警告 | 影响 |
| --- | --- |
| `Sliding Window Attention is enabled but not implemented for sdpa` | 无，仅实现路径不同 |
| `CUDAExecutionProvider is not in available provider` | onnxruntime 走 CPU，`campplus.onnx` 仅 27 MB，影响小 |
| `no frontend is avaliable` | 文本正则化用回退方案，**中文数字读法可能不完美** |
| `synthesis text ... too short than prompt text` | 待合成文本比参考文本短时的提示，实测未影响结果 |

---

## 五、待验证项（如实标注）

- [ ] **音频听感**：音质、自然度、音色相似度（需你实际听）
- [ ] **RTF 稳定性**：首次 1.168 是冷启动还是常态
- [ ] **长文本表现**：> 50 字时的 RTF 和稳定性
- [ ] **ASR + TTS 同时运行的显存竞争**（V2 第十五节硬要求）
- [ ] **文本正则化质量**：数字、符号、英文混读
- [ ] **与开发板播放格式的适配**：24000 Hz 需转成开发板支持的格式

---

## 六、文件位置

| 内容 | 路径 |
| --- | --- |
| 模型 | `~/models/Fun-CosyVoice3-0.5B-2512`（WSL 内） |
| 仓库 | `~/CosyVoice`（WSL 内） |
| 虚拟环境 | `~/cosyvoice-venv`（WSL 内） |
| 合成音频 | `D:\xiangmudaima\zjy13\logs\tts\`（Windows） |
| 安装脚本 | `scripts/setup_cosyvoice_wsl.sh` |
| 修复脚本 | `scripts/repair_cosyvoice_wsl.sh` |
| 测试脚本 | `scripts/test_cosyvoice_wsl.sh` |
| 性能脚本 | `scripts/bench_cosyvoice_wsl.sh` |

---

## 七、复现命令

```powershell
# 合成测试
wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/test_cosyvoice_wsl.sh

# 性能基线
wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/bench_cosyvoice_wsl.sh
```

Windows 端播放：

```
D:\xiangmudaima\zjy13\logs\tts\cosyvoice_zero_shot.wav
```
