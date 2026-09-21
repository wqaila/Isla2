# merge_and_convert_model.py 使用指南

## 概述

`merge_and_convert_model.py` 是一个一站式工具脚本，用于将 LoRA 微调模型与基模型合并，并转换为 llama.cpp 可使用的 GGUF 格式。支持自由选择执行合并、GGUF 转换、量化三个步骤中的任意组合。

## 支持的量化级别

| 量化级别 | 描述 | 模型大小（约） | 速度 | 质量 |
|----------|------|----------------|------|------|
| `q4_0` | 4-bit 量化（推荐） | ~4GB | 快 | 中 |
| `q4_1` | 4-bit 量化（改进版） | ~4.5GB | 快 | 中高 |
| `q5_0` | 5-bit 量化 | ~5.5GB | 中 | 高 |
| `q5_1` | 5-bit 量化（改进版） | ~6GB | 中 | 高 |
| `q8_0` | 8-bit 量化 | ~8GB | 中 | 很高 |
| `f16` | 16-bit 浮点（无量化） | ~14GB | 慢 | 最高 |

**推荐**: `q4_0`（平衡速度、大小和质量，适合 Android 手机部署）

## 使用方法

### 1. 全部流程（合并 + 转换 + 量化）

将 LoRA 权重合并到基模型，然后转换为 GGUF 格式并量化：

```bash
python merge_and_convert_model.py --merge --convert --quantize q4_0
```

不指定 `--merge`/`--convert` 时，默认执行全部流程：

```bash
python merge_and_convert_model.py --quantize q4_0
```

### 2. 仅合并（不转换 GGUF）

将 LoRA 权重合并到基模型，保存为 HuggingFace 格式：

```bash
python merge_and_convert_model.py --merge --output models/gguf/merged_model
```

### 3. 仅转换（不合并）

将已有的 HuggingFace 模型转换为 GGUF 格式：

```bash
python merge_and_convert_model.py --convert --model models/gguf/merged_model/merged_model --output models/gguf --quantize q4_0
```

### 4. 仅量化（对已有 GGUF 文件）

对已有的 GGUF 文件进行量化（无需合并或转换）：

```bash
python merge_and_convert_model.py --quantize-only --gguf-input models/gguf/model-f16.gguf --quantize q4_0
```

### 5. 转换后复制到 Android assets

```bash
python merge_and_convert_model.py --convert --model models/gguf/merged_model/merged_model --quantize q4_0 --copy-assets
```

## 全部参数说明

### 步骤选择参数

| 参数 | 说明 |
|------|------|
| `--merge` | 执行 LoRA 合并步骤（将 LoRA 权重合并到基模型） |
| `--convert` | 执行 GGUF 转换步骤（将模型转换为 GGUF 格式） |
| `--quantize-only` | 仅执行量化步骤（对已有 GGUF 文件进行量化，需配合 `--gguf-input` 使用） |

### 合并相关参数

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--lora` | `-l` | `models/lora_output` | LoRA 模型路径 |
| `--base` | `-b` | `models/google/gemma-4-E2B-it` | 基模型路径 |

### 转换相关参数

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--model` | `-m` | 无 | 直接指定要转换的模型路径（仅 `--convert` 时使用） |

### 量化相关参数

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--gguf-input` | 无 | 无 | 已有 GGUF 文件路径（仅 `--quantize-only` 时使用） |
| `--quantize` | `-q` | `q4_0` | 量化级别：`q4_0`/`q4_1`/`q5_0`/`q5_1`/`q8_0` |

### 通用参数

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--output` | `-o` | `models/gguf` | 输出路径 |
| `--skip-quantize` | 无 | 否 | 跳过量化步骤（仅对 `--convert` 步骤生效） |
| `--copy-assets` | 无 | 否 | 复制模型到 Android assets 目录 |
| `--build` | 无 | 否 | 先编译 llama.cpp（用于量化） |

## 项目依赖

### Python 依赖

合并步骤需要：
- `torch`
- `transformers`
- `safetensors`
- `peft`

转换步骤需要：
- `absl-py`
- `numpy`
- `torch`
- `transformers`
- `sentencepiece`
- `protobuf`

量化步骤需要：
- llama.cpp 编译产物（`llama-quantize` 工具）

### llama.cpp 依赖

量化功能需要先编译 llama.cpp：

```bash
# 方式一：使用 --build 参数自动编译
python merge_and_convert_model.py --quantize-only --gguf-input models/gguf/model-f16.gguf --quantize q4_0 --build

# 方式二：手动编译
cd android_app/app/llama.cpp
cmake -B build -S .
cmake --build build --config Release -j
```

## 工作流程图

```
┌─────────────────────────────────────────────────────────────┐
│                    merge_and_convert_model.py                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐              │
│  │  --merge  │───▶│ --convert│───▶│ --quantize│             │
│  │  LoRA合并 │    │ GGUF转换 │    │   量化    │             │
│  └──────────┘    └──────────┘    └──────────┘              │
│       │               │               │                    │
│       ▼               ▼               ▼                    │
│  基模型+LoRA      HuggingFace      GGUF F16       GGUF Q4 │
│  → 合并模型       → GGUF F16       → 量化模型      → 部署  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │           --quantize-only（独立量化）                   │  │
│  │   已有 GGUF F16 ──▶ 量化 ──▶ GGUF Q4/Q5/Q8          │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## 常见问题

### Q: 合并时出现 DLL 加载错误？
A: 安装 Visual C++ Redistributable，或重新安装 PyTorch：
```bash
pip uninstall torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

### Q: 量化工具未找到？
A: 需要先编译 llama.cpp，使用 `--build` 参数或手动编译：
```bash
cd android_app/app/llama.cpp
cmake -B build -S .
cmake --build build --config Release -j
```

### Q: 内存不足导致合并失败？
A: 合并大模型需要较多内存。建议：
- 关闭其他程序释放内存
- 使用 `--dtype float16`（默认）
- 确保有足够的磁盘空间（至少模型大小的 2 倍）

### Q: 如何选择量化级别？
A: 根据设备内存和需求选择：
- **手机部署（8GB RAM）**: `q4_0`（推荐）
- **平板/高性能设备（12GB+ RAM）**: `q5_1` 或 `q8_0`
- **PC 端推理**: `f16` 或 `q8_0`

## 相关文件

- `convert_to_gguf.py` — 独立的 GGUF 转换工具
- `merge_lora_only.py` — 独立的 LoRA 合并工具
- `download_qwen_model.py` — 模型下载工具
- `token_truncation_tool.py` — Token 截断工具
