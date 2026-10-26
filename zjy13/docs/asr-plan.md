# 本地 ASR 方案（阶段一）

> 对应方案文档第三节、第十四节阶段一、第十五节性能验收。
> 本文件先写**方案与依据**，实测数据在验证通过后补充，不预填、不编造。

---

## 一、环境实测结论（2026-10-23 本机）

| 项 | 实测值 | 说明 |
| --- | --- | --- |
| 操作系统 | Windows（版本读取被权限拒绝，待补） | `Win32_OperatingSystem` 查询被拒 |
| Python | 3.11.9 | 系统安装于 `C:\Users\user\AppData\Local\Programs\Python\Python311` |
| GPU | NVIDIA GeForce RTX 5070 | 12227 MiB 显存 |
| 驱动 | 610.62 | CUDA UMD 13.3 |
| **计算能力** | **sm_120 (12.0)** | **关键约束，见下节** |
| 磁盘 | C: 剩 286 GB，D: 剩 1247 GB | 充足 |

### 关键发现：RTX 5070 是 Blackwell，需要新版 PyTorch

`compute_cap = 12.0`，属于 Blackwell 架构。

**实测证据**：`zjy5\.venv` 里的 `torch 2.1.0+cu118` 虽然 `torch.cuda.is_available()` 返回
`True`，但加载时警告：

```
NVIDIA GeForce RTX 5070 with CUDA capability sm_120 is not compatible with the current
PyTorch installation. The current PyTorch install supports CUDA capabilities
sm_37 sm_50 sm_60 sm_61 sm_70 sm_75 sm_80 sm_86 sm_90 compute_37
```

而 `torch.cuda.get_arch_list()` 确认**不含 sm_120**。

> ⚠️ 陷阱：此时简单 `matmul` 仍能算出一个结果（PyTorch 回退到 PTX JIT 编译），
> 很容易误判为"GPU 可用"。实际跑模型会出现算子缺失或极慢。
> **这正是方案文档警告的"不要直接拼接教程版本号"。**

### 结论：必须安装 CUDA 12.8 及以上构建的 PyTorch ≥ 2.7.0

`sm_120` 支持从 PyTorch 2.7.0 + cu128 开始。本项目选定 **torch 2.8.0+cu128**。

---

## 二、版本选型依据

| 组件 | 选定版本 | 依据 |
| --- | --- | --- |
| Python | 3.11.9 | 系统已有；FunASR `requires_python>=3.7` 满足 |
| PyTorch | 2.8.0+cu128 | 支持 sm_120；2.7.0 是首发支持但生态磨合不足；2.9+ 较新，与 FunASR 旧 API 冲突风险高 |
| FunASR | 1.4.16（最新） | 依赖列表**不含 torch**，不会强制降级我们的 CUDA 版本 |
| 模型 | Paraformer-large 中文 | 方案文档指定 FunASR Paraformer 中文 |

### 为什么把 torch 和 FunASR 装在独立环境

方案文档第三节要求"建立独立的 Python 虚拟环境"。

- `zjy13/.venv` —— 云端 LLM 模块，已验证的依赖（httpx/pyyaml/dotenv/pytest），**不能污染**
- `zjy13/.venv-asr` —— 本阶段新建，只装 ASR 相关

---

## 三、模型清单（已确认在 ModelScope 可访问）

| 模型 ID | 用途 | 状态 |
| --- | --- | --- |
| `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch` | 主 ASR 模型 | ✅ 可访问 |
| `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` | VAD 语音活动检测 | ✅ 可访问 |
| `iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch` | 标点恢复 | ✅ 可访问 |
| `iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch` | 一体化（含 VAD+Punc） | ✅ 可访问 |

**第一版策略**：先用**主 ASR 模型**跑通识别（对应文档"第一版先跑通普通话音频的离线识别"），
VAD 与标点恢复作为可选增强，确认主链路稳定后再加。

---

## 四、音频格式约定

方案文档第四节要求：

- ASR 输入统一为 **16 kHz、16-bit signed PCM、单声道**
- 开发板实际采集格式需按硬件驱动确认，由电脑端做重采样/声道转换
- 格式检查必须记录转换前后的格式

第一版处理链：

```
输入 WAV → 格式探测（采样率/位宽/声道）
        → 不满足则重采样到 16k / 单声道 / 16-bit
        → 规范化 WAV（临时文件）
        → FunASR 识别
        → 记录：原始格式、转换后格式、音频时长、推理耗时
```

**必须处理的异常**（文档 3.2）：

| 情况 | 处理 |
| --- | --- |
| 空音频 | 直接报错，不送模型 |
| 过短（< 0.2s） | 报错并提示 |
| 采样率不符 | 重采样，记录前后值 |
| 多声道 | 降混为单声道，记录 |
| 位宽不符 | 转换 |
| 噪声过大 / 无有效语音 | 有 VAD 时提示"未检测到语音" |

---

## 五、目录结构（本阶段新增）

```
zjy13/
├─ .venv-asr/                 # ASR 独立环境（不入库）
└─ asr/
   ├─ __init__.py
   ├─ engine.py               # FunASR 封装：加载一次、复用、预热
   ├─ audio_utils.py          # 格式探测、重采样、声道转换、时长
   ├─ vad.py                  # 可选：VAD 分段
   ├─ transcribe.py           # CLI：输入 WAV 输出文本与计时
   └─ README.md               # 本阶段使用说明
```

与既有 `server/llm/` 并列，互不依赖。

---

## 六、验收标准（文档阶段一 9 条）

| # | 要求 | 验证方式 |
| --- | --- | --- |
| 1 | 检查 Python/PyTorch/CUDA/显卡环境 | 本文件第一节，脚本输出 |
| 2 | 建立独立虚拟环境 | `.venv-asr`，`pip list` 截图 |
| 3 | 安装兼容的 FunASR Paraformer 依赖 | 装完跑通 import + 模型加载 |
| 4 | 验证 RTX 5070 GPU 调用 | 必须打印 `torch.cuda.get_device_name(0)` 且**确认 arch_list 含 sm_120**，并用真实模型推理验证非回退 |
| 5 | 下载并缓存模型 | 指定缓存目录，二次加载明显更快 |
| 6 | 中文 WAV 完成识别 | 用固定测试音频，输出文本 |
| 7 | 记录识别文本/音频时长/推理时间/设备信息 | 结构化日志 |
| 8 | 建立初步性能基线 | 多次运行取均值与 P95 |
| 9 | 可复现命令与故障排查 | `asr/README.md` |

**特别注意第 4 条**：不能只凭 `torch.cuda.is_available()` 判定通过——
本项目已实测出该值在 sm_120 不匹配时仍返回 `True`。必须校验 `get_arch_list()` 含 `sm_120`，
并用真实模型推理验证。

---

## 七、当前进度

| 步骤 | 状态 |
| --- | --- |
| 环境审计 | ✅ 完成 |
| 识别 sm_120 约束 | ✅ 完成（实测） |
| 版本选型 | ✅ 完成 |
| 模型可用性确认 | ✅ 完成 |
| 创建 `.venv-asr` | ✅ 完成 |
| 安装 torch 2.8.0+cu128 | ⏳ 下载中（3461 MB） |
| 安装 FunASR | ⬜ 待做 |
| 模型下载与加载 | ⬜ 待做 |
| 真实 WAV 识别 | ⬜ 待做 |
| 性能基线 | ⬜ 待做 |

> 网络实测：阿里云镜像 0.03 MB/s（不可用），`download-r2.pytorch.org` 1.73 MB/s。
> 已切换为官方 CDN 下载。
