# 本地 ASR（FunASR Paraformer 中文）

> 阶段一产物。方案与依据见 [../docs/asr-plan.md](../docs/asr-plan.md)。

---

## 1. 环境要求（关键）

本机 **RTX 5070 是 Blackwell 架构（sm_120）**，必须使用 **CUDA 12.8+ 构建的 PyTorch ≥ 2.7.0**。

旧版 PyTorch（如 2.1.0+cu118）会表现为：

```
NVIDIA GeForce RTX 5070 with CUDA capability sm_120 is not compatible with the current
PyTorch installation. The current PyTorch install supports CUDA capabilities
sm_37 sm_50 sm_60 sm_61 sm_70 sm_75 sm_80 sm_86 sm_90 compute_37
```

⚠️ 此时 `torch.cuda.is_available()` **仍返回 True**，简单 matmul 甚至能算出结果
（走 PTX JIT），很容易误判为"GPU 正常"。实际跑模型会极慢或算子缺失。

因此本项目在 `check_device()` 里额外校验 `get_arch_list()` 是否包含 `sm_120`，
不匹配直接报错，不做静默回退。

---

## 2. 安装

ASR 使用**独立虚拟环境** `.venv-asr`，与云端 LLM 的 `.venv` 隔离。

```powershell
cd D:\xiangmudaima\zjy13

# 1) 创建环境
python -m venv .venv-asr

# 2) 安装 torch（必须 cu128 构建）
.\.venv-asr\Scripts\python.exe -m pip install --timeout 120 `
    --index-url https://download.pytorch.org/whl/cu128 `
    torch==2.8.0+cu128

# 3) 安装 FunASR 及其依赖
.\.venv-asr\Scripts\python.exe -m pip install --timeout 60 `
    -i https://pypi.tuna.tsinghua.edu.cn/simple `
    funasr modelscope numpy
```

> 网络说明（本机实测）：阿里云 pytorch 镜像只有 0.03 MB/s，不可用；
> `download-r2.pytorch.org` 单连接约 0.15 MB/s，多线程分块可达 3.6 MB/s。
> 大文件下载可用 `tools/fetch_wheel.py`。

---

## 3. 使用

### 检查环境（不加载模型）

```powershell
.\.venv-asr\Scripts\python.exe -m asr.transcribe --check-env
```

输出应包含 `架构匹配 : True`。若为 `False`，说明 PyTorch 版本不对。

### 识别单个文件

```powershell
.\.venv-asr\Scripts\python.exe -m asr.transcribe path\to\audio.wav
```

### 批量识别目录

```powershell
.\.venv-asr\Scripts\python.exe -m asr.transcribe tests\audio --pattern *.wav
```

### 输出 JSON（便于脚本消费）

```powershell
.\.venv-asr\Scripts\python.exe -m asr.transcribe audio.wav --json
```

### 常用参数

| 参数 | 说明 |
| --- | --- |
| `--check-env` | 只检查环境 |
| `--cpu` | 允许 CPU 回退 |
| `--no-vad` | 关闭 VAD |
| `--punc` | 启用标点恢复 |
| `--cache-dir` | 指定模型缓存目录 |
| `--json` | JSON 输出 |
| `-v` | 调试日志 |

---

## 4. 输出字段

| 字段 | 含义 |
| --- | --- |
| `text` | 识别文本 |
| `audio_duration_s` | 音频时长（秒） |
| `infer_ms` | 模型推理耗时 |
| `total_ms` | 含格式转换的总耗时 |
| `realtime_factor` | RTF = 推理耗时 / 音频时长，越小越快 |
| `device` | torch/CUDA/GPU/架构匹配情况 |
| `normalize` | 转换前后的格式记录 |

---

## 5. 音频格式

- ASR 输入统一为 **16 kHz / 16-bit / 单声道**
- 输入不是该格式时自动转换，并在 `normalize` 字段记录前后格式
- 拒绝：空文件、非 PCM WAV、短于 0.2s、几乎静音

---

## 6. 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `CUDA 不可用` | torch 是 CPU 版或驱动问题 | 装 cu128 版本；确认 `nvidia-smi` 正常 |
| `PyTorch 编译的架构列表不包含 sm_120` | torch 版本太旧 | 装 `torch==2.8.0+cu128` |
| 识别极慢（RTF > 1） | 回退到了 PTX JIT | 同上，检查 `--check-env` 的架构匹配 |
| `未安装 FunASR` | 依赖没装 | `pip install funasr` |
| 模型下载卡住 | ModelScope 网络 | 设置 `MODELSCOPE_CACHE`，或手动下载模型 |
| `音频过短` | 小于 0.2s | 检查录音是否正常 |
| `音频几乎静音` | 空录音 | 检查麦克风 |
| `不是标准 PCM WAV` | 压缩格式 | 先用 ffmpeg 转 PCM WAV |
