# 阶段一：本地 ASR 验收报告

> 状态：**通过**。以下全部为本机真实执行结果，未使用任何模拟数据。

---

## 一、环境

| 项 | 值 |
| --- | --- |
| Python | 3.11.9（`.venv-asr` 独立环境） |
| PyTorch | 2.8.0+cu128 |
| CUDA | 12.8 |
| GPU | NVIDIA GeForce RTX 5070，12227 MiB |
| 计算能力 | **sm_120（Blackwell）** |
| 驱动 | 610.62 |
| FunASR | 1.4.16 |
| 模型 | Paraformer-large 中文（889 MB） |

### GPU 验证（关键）

```
torch          : 2.8.0+cu128
CUDA 版本      : 12.8
CUDA 可用      : True
GPU            : NVIDIA GeForce RTX 5070
计算能力       : sm_120
编译架构列表   : sm_61, sm_70, sm_75, sm_80, sm_86, sm_90, sm_100, sm_120
架构匹配       : True
实际使用       : GPU
```

**为什么必须校验架构列表**：本机原有的 `torch 2.1.0+cu118` 在 RTX 5070 上
`is_available()` 也返回 `True`，但 `arch_list` 最高只到 `sm_90`，实际会静默回退到
PTX JIT。仅看 `is_available()` 会误判为"GPU 正常"。

---

## 二、识别结果（真实）

测试音频：ModelScope 官方示例 `asr_example.wav`（16 kHz / 16-bit / 单声道 / 5.55 s）

```
识别: 欢迎大家来体验达摩院推出的语音识别模型
时长 5.55s | 推理 85ms | RTF 0.015 | 总 92ms
```

**识别完全正确，与官方示例文本一致。**

---

## 三、性能基线（5 次重复，预热后）

| 指标 | 值 |
| --- | --- |
| 模型加载 | 1695 ms |
| 预热 | 456 ms |
| 音频时长 | 5.547 s |
| **推理耗时 mean** | **49.7 ms** |
| 推理耗时 min / max | 45.7 / 56.0 ms |
| **RTF mean** | **0.009** |
| 端到端 mean（含格式检查） | 56.5 ms |

> RTF = 0.009 表示处理速度约为实时的 **111 倍**。
> 5.5 秒音频只需 50 毫秒，ASR 不是端到端延迟的瓶颈。

---

## 四、踩过的坑与解决办法（可复现）

### 坑 1：PyTorch 版本与 sm_120 不兼容

**现象**：`torch.cuda.is_available()` 为 True，简单 matmul 也能算，但加载模型失败或极慢。

**解决**：装 `torch==2.8.0+cu128`。sm_120 需要 CUDA 12.8 构建且 PyTorch ≥ 2.7.0。

### 坑 2：pip 的 TLS 被中间设备拦截

**现象**：`SSL: UNEXPECTED_EOF_WHILE_READING`，但同一台机器用 httpx 访问相同 URL 完全正常。

**解决**：写了 `tools/pip_fetch.py`，用 httpx 解析 PyPI JSON 下载 wheel，再本地安装。

### 坑 3：大文件下载极慢

**实测速度**：

| 源 | 速度 |
| --- | --- |
| 阿里云 pytorch 镜像 | 0.03 MB/s（不可用） |
| download.pytorch.org 单连接 | 0.15 MB/s |
| download-r2.pytorch.org 分块并发 | **3.6 MB/s** |

**解决**：写了 `tools/fetch_wheel.py`，Range 分块 + 24 并发。3461 MB 的 torch 用 17 分钟下完。

### 坑 4：ModelScope 缓存路径不可写（重要）

**现象**：

```
[WinError 5] 拒绝访问。: 'C:\Users\user\.modelscope'
```

即使设置了 `MODELSCOPE_CACHE` 环境变量、甚至给 `AutoModel` 传了 `cache_dir`，仍然报错。

**根因**：FunASR 的 `get_or_download_model_dir()` 调用 `snapshot_download()` 时
**没有传 cache_dir**，ModelScope 使用默认路径。而本机 `C:\Users\user` 下
**任何新目录都无法创建**（普通目录也不行，ACL 显示 FullControl 但实际被策略拦截）。

**解决**：用 `tools/fetch_model.py` 把模型文件直接下载到 `_models/<模型名>/`，
然后把**本地目录路径**传给 `AutoModel(model=...)`。FunASR 检测到路径已存在，
不再走下载分支。

### 坑 5：音频读取缺少后端

**现象**：

```
RuntimeError: Couldn't find appropriate backend to handle uri ...wav
OSError: cannot load library 'libsndfile.dll'
```

**根因**：FunASR 读文件依赖 `soundfile`（需要 `libsndfile.dll`）或 `ffmpeg`，
本机两者都没有。新版 torchaudio 也移除了内置 WAV 解码器。

**解决**：改用 numpy 数组传参。`generate()` 接受 `ndarray`（float32，[-1,1]），
用自己写的 `asr/audio_utils.read_pcm()`（纯标准库 `wave` + numpy）读取，
彻底绕开 soundfile 和 ffmpeg。

**注意**：必须传 `float32`，传 `int16` 会报
`NotImplementedError: "normal_kernel_cpu" not implemented for 'Short'`。

---

## 五、VAD 实测观察

启用 VAD 后：

| 配置 | 推理耗时 | 识别结果 |
| --- | --- | --- |
| 无 VAD | 85 ms | 欢迎大家来体验达摩院推出的语音识别模型 ✅ |
| 有 VAD | 368 ms | 欢迎大家来体验达摩院推出的语音识别**型儿** ❌ |

VAD 切分导致末尾"模型"被截断成"型儿"，且耗时增加 4 倍。

**结论**：第一版**默认关闭 VAD**。等接入真实麦克风录音（可能有长静音段）再评估。
当前 `asr/transcribe.py` 默认 `--no-vad` 更稳。

---

## 六、代码位置

| 文件 | 作用 |
| --- | --- |
| `asr/audio_utils.py` | 格式探测、重采样、声道转换、规范化、异常校验 |
| `asr/engine.py` | FunASR 封装：加载一次、预热、GPU 校验、计时 |
| `asr/transcribe.py` | CLI 入口 |
| `tools/fetch_model.py` | 绕开 ModelScope 缓存直接下载模型 |
| `tools/pip_fetch.py` | 绕开 pip TLS 安装依赖 |
| `tools/fetch_wheel.py` | 大文件分块并发下载 |

---

## 七、测试

```
.\.venv-asr\Scripts\python.exe -m pytest tests/test_audio_utils.py tests/test_asr_engine.py -q
28 passed
```

覆盖：格式探测、重采样、声道降混、位宽转换、过短/静音拒绝、sm_120 架构校验、
模型幂等加载、缺失依赖报错。

---

## 八、未验证项（如实标注）

- **真实麦克风录音**：只用了官方示例 WAV，未接入 ESP32 采集的音频
- **长音频**（> 1 分钟）：未测
- **噪声环境**：未测
- **VAD 在真实录音上的表现**：未测
- **ASR + TTS 同时运行的显存竞争**：V2 新要求，待 TTS 就绪后测

---

## 九、复现命令

```powershell
cd D:\xiangmudaima\zjy13
$env:PYTHONUTF8="1"

# 检查环境
.\.venv-asr\Scripts\python.exe -m asr.transcribe --check-env

# 识别
.\.venv-asr\Scripts\python.exe -m asr.transcribe "tests\audio\asr_example.wav" --no-vad

# 测试
.\.venv-asr\Scripts\python.exe -m pytest tests/test_audio_utils.py tests/test_asr_engine.py -q
```
