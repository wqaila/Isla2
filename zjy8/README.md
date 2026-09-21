# 方案一：本地电脑推理 + 手机远程调用

## 本项目（zjy8）负责什么

zjy8 只负责整条链路的**首尾两端**：

- 下载 HuggingFace 格式的基模型（`download_model.py`）
- 把已经量化好的 GGUF 跑成本地推理服务，供手机调用（`start_server.py` / `Modelfile`）
- 附带一组 GGUF 元信息检查与修复工具（`gguf_meta.py` / `check_model.py` / `dump_tensors.py` / `verify_gguf.py` / `fix_gguf_final.py`）

**中间的 LoRA 训练、合并、GGUF 转换与量化不在本项目里**，而是在同级目录的 zjy7 / zjy6 中完成
（本项目旧版 README 里写的 `train_lora.py`、`merge_model.py`、`convert_to_gguf.py`、`quantize_model.py`
在 zjy8 目录下并不存在，容易误导，这里已按实际情况更正）。

## 完整流程概览

```
┌─────────────────────────────────────────────────────────────┐
│                 LoRA 微调 + 本地部署完整流程                  │
│                                                             │
│  ① 下载基模型 (HuggingFace 格式, ~14GB)      ← zjy8          │
│     python download_model.py --mirror modelscope            │
│     产出: models/base_model/                                │
│              ↓                                              │
│  ② 准备语料库 (角色对话数据, JSON 格式)                      │
│     zjy8/train_data.json、zjy8/10.txt 为爱莉希雅角色语料      │
│              ↓                                              │
│  ③ LoRA 微调训练 (需 GPU)                    ← zjy7          │
│     zjy7/train_lora.py                                      │
│     产出: zjy7/lora_output/ (adapter 权重)                   │
│              ↓                                              │
│  ④ 合并 LoRA + 基模型，并转换 / 量化成 GGUF   ← zjy6          │
│     zjy6/merge_and_convert_model.py --merge --convert        │
│                                       --quantize q8_0       │
│     (也可拆开用 zjy6/merge_lora_only.py + zjy6/convert_to_gguf.py) │
│     产出: zjy7/gguf_output/model-Q8_0.gguf 等                │
│              ↓                                              │
│  ⑤ 启动本地推理服务                          ← zjy8          │
│     A. llama-server: python start_server.py  (默认端口 8080) │
│     B. Ollama:       ollama create my-llm -f Modelfile       │
│                      (Ollama 默认端口 11434)                 │
│              ↓                                              │
│  ⑥ 手机端通过网络调用电脑 API                                │
└─────────────────────────────────────────────────────────────┘
```

## 硬件要求

| 项目 | 最低要求 | 推荐配置 |
|------|----------|----------|
| GPU | 无（CPU 推理慢） | RTX 3060 12GB |
| 内存 | 8GB | 16GB+ |
| 硬盘 | 30GB 可用空间 | 50GB+ SSD |

## 当前实际可用的模型

| 文件 | 说明 |
|------|------|
| `gemma-4-E2B-it-Q5_K_S.gguf` | **本目录唯一存在的 GGUF**（Gemma-4-E2B-It，Q5_K_S 量化，约 3.09GB），`Modelfile` 指向它 |
| `models/base_model/` | 已下载完成的 Qwen2.5-7B-Instruct HuggingFace 基模型（4 个 safetensors 分片，约 14.2GB），用于 LoRA 训练，本项目内**未**转换成 GGUF |
| `zjy7/gguf_output/model-Q8_0.gguf` | Qwen 侧的 Q8_0 量化产物，位于 zjy7 项目 |
| `models/llama-b9222-bin-win-cpu-x64/` | 自带的 llama.cpp Windows CPU 版发布包，含 `llama-server.exe`，`start_server.py` 会自动找到它 |

> 注意：README 里的性能数据、`Modelfile` 都应以**实际存在的 GGUF 文件**为准。
> 目前实际跑的是 Gemma-4-E2B-It Q5_K_S；Qwen2.5-7B-Instruct 只是训练用的基模型。

## ⑤ 启动推理服务

### 方式 A：llama-server（`start_server.py`，默认端口 8080）

```bash
# 默认：自动查找模型和 llama-server，仅本机可访问（127.0.0.1）
python start_server.py

# 指定模型 / 端口 / 线程
python start_server.py --model gemma-4-E2B-it-Q5_K_S.gguf --port 8080 --threads 8

# GPU 加速（层数按显存调整，见下文）
python start_server.py --gpu-layers 20

# 仅 CPU 推理（默认值）
python start_server.py --gpu-layers 0
```

**安全说明（重要）**

- 默认只监听 `127.0.0.1`，仅本机可访问，这是最安全的用法。
- 如果显式指定 `--host 0.0.0.0`（让局域网内的手机直连），脚本会**强制要求**提供 `--api-key`，
  否则直接报错退出——避免把推理服务以无认证方式暴露给整个局域网。
  生成密钥：

  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  python start_server.py --host 0.0.0.0 --api-key <上一步生成的密钥>
  ```

  客户端调用时需要带上 `Authorization: Bearer <密钥>`。
- 也可以设置环境变量 `ZJY8_API_KEY` 代替 `--api-key`。

**GPU 层数怎么调**

`--gpu-layers` 默认 `0`（纯 CPU），这是保守值，避免在小显存机器上直接 OOM。
经验参考：RTX 3060 12GB 跑 7B 的 Q4/Q5 量化模型大致可以放 20~35 层，具体请按实际显存占用逐步往上加，
显存不够时把层数调小或改用更小的量化（Q4_K_M 比 Q5_K_S 更省显存）。

**模型 / 服务端路径如何配置**

不再依赖 `../zjy6/...` 这类跨项目相对路径，改为三级可配置：

1. 命令行：`--model`、`--server`、`--models-dir`、`--llama-dir`
2. 环境变量：`ZJY8_MODEL_DIRS`（多个目录用 `;` 分隔）、`ZJY8_LLAMA_SERVER`
3. 默认值：基于脚本所在目录推导（本目录、`models/gguf/`、`models/`）

找不到模型或 `llama-server` 时会打印清晰的提示与候选路径，而不是直接崩溃。

### 方式 B：Ollama（默认端口 11434）

```bash
# 导入 GGUF 模型到 Ollama（Modelfile 指向本目录实际存在的 GGUF）
ollama create my-llm -f Modelfile

# 测试推理
ollama run my-llm "你好，请介绍一下自己"
```

Ollama 自带 GPU 支持、无需手动编译 llama.cpp，并自动提供 OpenAI 兼容 API：

- 默认地址：`http://localhost:11434`
- 服务启动后即可使用

## 手机端连接方式

### 方式一：WiFi 局域网连接

```bash
# 1. 确保手机和电脑在同一 WiFi 网络
# 2. 查看电脑 IP 地址
ipconfig
```

推理服务需要监听局域网地址才能被手机直连（注意这会对外暴露服务，必须带 api-key）：

```bash
# llama-server：默认端口 8080
python start_server.py --host 0.0.0.0 --api-key <密钥>
# 手机访问 http://<电脑IP>:8080

# Ollama：默认端口 11434
# 手机访问 http://<电脑IP>:11434
```

### 方式二：USB 有线连接（推荐，延迟最低）

USB 转发不需要对外监听，服务保持默认的 `127.0.0.1` 即可：

```bash
# 1. 手机通过 USB 连接电脑
# 2. 使用 adb 端口转发（端口按实际使用的服务填 8080 或 11434）
adb reverse tcp:8080 tcp:8080

# 3. 手机访问
# http://localhost:8080
```

### API 调用示例

```bash
# llama-server（start_server.py 默认端口 8080）
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <你的 api-key>" \
  -d '{
    "messages": [{"role": "user", "content": "你好"}]
  }'

# Ollama（默认端口 11434）
curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "my-llm",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

## 推理性能（当前测试结果）

| 指标 | 数值 |
|------|------|
| 模型 | Gemma-4-E2B-it Q5_K_S |
| 推理方式 | CPU（Ollama） |
| 生成速度 | **4.13 tokens/s** |
| 首 token 延迟 | ~12s（模型加载） |

> **提示**: 当前使用的是 Gemma-4-E2B 小模型、且跑在 CPU 上，速度较慢。
> 如果换成 Qwen2.5-7B-Instruct（LoRA 微调并量化后），配合 RTX 3060 GPU 加速，
> 预期速度可达 **20-35 tokens/s**。

## GGUF 工具说明

| 脚本 | 用途 |
|------|------|
| `gguf_meta.py` | 流式读取 GGUF 元信息的公共模块（只读文件头/KV/tensor_info，不载入数 GB 数据） |
| `check_model.py` | 打印 GGUF 的文件头与全部 KV |
| `dump_tensors.py` | 打印张量清单，并检查文件是否被截断 |
| `verify_gguf.py` | 校验关键 KV 是否齐全 |
| `fix_gguf_final.py` | 补写缺失的 KV（默认 `tokenizer.ggml.model = "gpt2"`） |

用法示例：

```bash
python check_model.py -i gemma-4-E2B-it-Q5_K_S.gguf
python dump_tensors.py --list
python verify_gguf.py
python fix_gguf_final.py -i model.gguf -o model-fixed.gguf
```

> `fix_gguf_final.py` 不会就地修改输入文件，输出默认写到 `<输入名>-fixed.gguf`，
> 并在写完后自检「张量 offset 是否与原文件完全一致、数据段是否仍然对齐」。

## 文件说明

| 文件 | 用途 |
|------|------|
| `download_model.py` | 下载基模型（支持国内镜像，断点续传默认开启） |
| `fix_download.py` | 补下载缺失的 tokenizer 文件 |
| `start_server.py` | llama-server 本地推理服务启动脚本（默认端口 8080） |
| `Modelfile` | Ollama 模型配置文件（指向本目录实际存在的 GGUF） |
| `gguf_meta.py` 等 4 个 GGUF 脚本 | GGUF 元信息检查与修复（见上一节） |
| `gemma-4-E2B-it-Q5_K_S.gguf` | 当前唯一可用的 GGUF 模型 |
| `models/base_model/` | 已下载的 Qwen2.5-7B-Instruct 基模型（用于训练，未转 GGUF） |
| `models/llama-b9222-bin-win-cpu-x64/` | llama.cpp Windows CPU 版发布包（含 llama-server.exe） |
| `train_data.json`、`10.txt` | 爱莉希雅角色语料（供 zjy7 训练使用） |
| `ollama-models/` | Ollama 模型仓库数据 |
| `README.md` | 本文档 |

## 常用 Ollama 命令

```bash
# 列出已安装的模型
ollama list

# 运行模型（交互式）
ollama run my-llm

# 运行模型（单次提问）
ollama run my-llm "你的问题"

# 删除模型
ollama rm my-llm

# 查看模型信息
ollama show my-llm

# 启动 Ollama 服务（如果未运行）
ollama serve
```
