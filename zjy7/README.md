# 爱莉希雅 LoRA 微调项目

基于 Qwen2.5-7B-Instruct 的 LoRA 微调，训练崩坏3角色「爱莉希雅」的 AI 角色扮演模型。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 生成训练数据
python prepare_data.py

# 3. 训练 LoRA
python train_lora.py

# 4. 合并 + GGUF转换 + 量化 + 测试
python post_train.py --quant Q8_0

# 5. 部署到 Ollama（可选）
python deploy_ollama.py
```

## 项目结构

```
├── train_config.json    # 训练配置（所有路径和参数集中管理，相对路径基于项目根目录）
├── config_utils.py      # 公共工具：配置加载 / 路径解析 / 随机种子
├── prepare_data.py      # 数据预处理
├── train_lora.py        # LoRA 训练（支持断点续训）
├── post_train.py        # 合并 → GGUF → 量化 → 测试
├── deploy_ollama.py     # Ollama Modelfile 自动生成
├── data_stats.py        # 训练数据统计分析
├── evaluate.py          # 模型评估工具（perplexity + 角色一致性 + 多轮对话 + 脱角色检测）
├── requirements.txt     # 依赖清单
├── train_data.json      # 生成的训练数据
├── eval_data.json       # 独立评估集（与训练集隔离，用于 perplexity）
├── Modelfile            # Ollama 部署文件（由 deploy_ollama.py 生成）
├── .gitignore           # 忽略模型/产物/虚拟环境等
├── downloads/           # 原始台词/对话数据
├── models/              # 基础模型 + llama.cpp 工具
│   ├── base_model/      # Qwen2.5-7B-Instruct 基础模型
│   └── llama-b9222-bin-win-cpu-x64/  # llama.cpp 工具
├── lora_output/         # LoRA adapter 输出 + 训练日志
├── merged_model/        # 合并后的完整模型
└── gguf_output/         # GGUF 模型输出
```

## 配置说明 (train_config.json)

### model 部分

| 参数 | 值 | 说明 |
|------|-----|------|
| base_model_path | models/base_model | 基础模型路径（相对项目根目录） |
| lora_r | 32 | LoRA 秩（降低以减少过拟合） |
| lora_alpha | 64 | LoRA 缩放因子 |
| lora_dropout | 0.15 | Dropout（增大以防过拟合） |

### data 部分

| 参数 | 值 | 说明 |
|------|-----|------|
| raw_data_dir | downloads | 原始台词/对话数据目录（prepare_data.py 的输入） |
| data_path | train_data.json | 生成的训练数据文件（prepare_data.py 输出 / train_lora.py 输入） |
| max_seq_length | 1024 | 最大序列长度（提升以支持多轮对话） |

> 配置中的相对路径统一以项目根目录（`config_utils.PROJECT_ROOT`）为基准解析，
> 因此可在任意工作目录下运行脚本。

### training 部分

| 参数 | 值 | 说明 |
|------|-----|------|
| num_train_epochs | 2 | 训练轮数（减少以防止过拟合） |
| per_device_train_batch_size | 1 | 单卡 batch |
| gradient_accumulation_steps | 16 | 梯度累积（有效batch=16） |
| learning_rate | 1e-4 | 学习率（降低以获得更稳定的训练） |
| warmup_ratio | 0.1 | 预热比例 |
| logging_steps | 5 | 每 N 步输出日志 |
| save_steps | 100 | 每 N 步保存 checkpoint |
| bf16 | true | 是否启用 bf16 混合精度 |
| gradient_checkpointing | true | 梯度检查点（节省显存） |
| weight_decay | 0.02 | 权重衰减（增大以增强正则化） |
| max_grad_norm | 1.0 | 梯度裁剪 |
| early_stopping_patience | 3 | 早停耐心值（更早停止以防止过拟合） |

### output 部分

| 参数 | 值 | 说明 |
|------|-----|------|
| output_dir | lora_output | LoRA adapter 和训练日志输出目录 |
| merged_dir | merged_model | LoRA 合并后的完整模型目录 |
| gguf_dir | gguf_output | GGUF 模型输出目录 |

### tools 部分

| 参数 | 值 | 说明 |
|------|-----|------|
| llama_quantize | models/llama-b9222-bin-win-cpu-x64/llama-quantize.exe | llama.cpp 量化工具路径 |
| llama_cli | models/llama-b9222-bin-win-cpu-x64/llama-cli.exe | llama.cpp 推理工具路径 |

## 各脚本说明

### prepare_data.py — 数据预处理

- 知识库（55条）+ 同义提问变体 + 高频 Q&A 重复
- 台词匹配使用**加权打分系统**（多条规则匹配时取得分最高的）
- **拒答/角色保护样本**（20条）：防止模型脱角色
- 多轮对话上下文窗口扩大到 5 轮
- 自动数据质量校验（结构、长度、内容检查 + 重复检测）

### train_lora.py — LoRA 训练

- 4-bit NF4 量化加载 Qwen2.5-7B-Instruct
- 训练 7 个模块：q/k/v/o/gate/up/down_proj
- **EarlyStopping** 防过拟合（patience=3）
- **验证集自动评估**（每 save_steps 步评估一次）
- 完整日志记录（文件 + 控制台）+ training_history.json 导出
- 异常样本逐条记录警告，不再静默跳过
- **支持断点续训**（`--resume` 参数）
- 代码重构：公共逻辑抽取为独立函数，消除重复代码

### post_train.py — 合并 + 转换 + 量化 + 测试

- **Step 1**: LoRA 合并到基础模型（使用 peft 库）
- **Step 2**: 转换为 GGUF F16（包含 chat_template 嵌入）
- **Step 3**: llama-quantize 量化
- **Step 4**: llama-cli 推理测试（6个测试问题）
- 所有路径从 train_config.json 读取，无硬编码
- 日志自动 flush（atexit 注册）

### deploy_ollama.py — Ollama 部署

- 自动生成 Modelfile（配置好的 Qwen2 模板 + 默认参数）
- 仅需运行 `ollama create elysia -f Modelfile` 即可部署
- 参数可自定义（温度、top_p、上下文长度等）

### data_stats.py — 训练数据统计

- 样本数、长度分布、估计 token 数
- 类别分布（身份、关系、世界观、感情、日常、拒答、对话上下文）
- 回复长度直方图
- 重复检测

### evaluate.py — 模型评估（增强版）

- **Perplexity 计算**：默认在**独立评估集 `eval_data.json`**（与训练集完全隔离）上计算困惑度
- **角色一致性评分**：通过 21 个典型问题，检查模型回复是否包含预期关键词
- **多轮对话测试**：3 组多轮连续对话，测试角色的一致性
- **脱角色检测**：通过 5 个诱导性问题，检查模型是否会在诱导下脱离角色
- 结果保存为 JSON，包含完整评估摘要

> ⚠️ **评估集隔离原则**：`evaluate.py` 中的测试用例与 `eval_data.json` 均在训练语料
> （`train_data.json` / `prepare_data.py` 的 KB、REJECTION_SAMPLES、SYNONYM_QUESTIONS）
> 之外独立构造，禁止从训练数据中挑选句子。若显式用 `--eval-data train_data.json`
> 计算 perplexity，脚本会打印训练集自评的告警。

## 命令行参数

### prepare_data.py

无命令行参数，直接运行即可。所有配置（知识库、匹配规则等）在脚本内部定义。

```bash
python prepare_data.py
```

输出：`train_data.json`

### train_lora.py

| 参数 | 类型 | 说明 |
|------|------|------|
| `--resume` | str（可选） | 断点续训。不指定路径则自动查找最新 checkpoint |

```bash
# 正常训练
python train_lora.py

# 断点续训（自动查找最新 checkpoint）
python train_lora.py --resume

# 断点续训（指定 checkpoint 路径）
python train_lora.py --resume lora_output/checkpoint-200
```

输出：`lora_output/` 目录（LoRA adapter + 训练日志 + training_history.json）

### post_train.py

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--quant` | str | Q8_0 | 量化级别。可选：F16, Q8_0, Q5_K_M, Q4_K_M 等 |
| `--skip-merge` | flag | false | 跳过 LoRA 合并步骤（已合并时使用） |
| `--skip-convert` | flag | false | 跳过 GGUF F16 转换步骤（已有 F16 文件时使用） |
| `--test-only` | str | - | 仅测试指定的 GGUF 文件，跳过合并/转换/量化 |

```bash
# 完整流程：合并 → 转换 → 量化(Q8_0) → 测试
python post_train.py --quant Q8_0

# 完整流程，使用更小的量化（质量更高，体积更大）
python post_train.py --quant Q5_K_M

# 跳过合并（已合并时）
python post_train.py --skip-merge --quant Q8_0

# 跳过 GGUF 转换（已有 F16 时）
python post_train.py --skip-merge --skip-convert --quant Q8_0

# 仅测试已有 GGUF 文件
python post_train.py --test-only gguf_output/model-Q8_0.gguf
```

### deploy_ollama.py

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--gguf` | str | - | GGUF 文件路径（默认自动查找） |
| `--output` | str | Modelfile | 输出路径 |
| `--temp` | float | 0.7 | 温度 |
| `--top-p` | float | 0.9 | top_p |
| `--num-ctx` | int | 2048 | 上下文长度 |
| `--gguf-dir` | str | gguf_output | GGUF 目录 |

```bash
# 自动生成 Modelfile（自动查找 GGUF）
python deploy_ollama.py

# 指定 GGUF 文件
python deploy_ollama.py --gguf gguf_output/model-Q8_0.gguf

# 自定义参数
python deploy_ollama.py --temp 0.5 --top-p 0.85 --num-ctx 4096

# 然后用 Ollama 部署
ollama create elysia -f Modelfile
ollama run elysia
```

### data_stats.py

```bash
python data_stats.py              # 默认分析 train_data.json
python data_stats.py other.json   # 分析指定文件
```

### evaluate.py

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--mode` | str | all | 评估模式：ppl / role / multi / escape / all |
| `--model-path` | str | - | LoRA adapter 路径（默认从config读取） |
| `--eval-data` | str | eval_data.json | 独立评估数据文件（用于perplexity，须与训练集隔离） |
| `--output` | str | eval_results.json | 结果输出文件 |

```bash
# 完整评估（perplexity + 角色一致性 + 多轮对话 + 脱角色检测）
python evaluate.py

# 仅计算 perplexity（默认使用独立评估集 eval_data.json）
python evaluate.py --mode ppl

# 仅测试角色一致性
python evaluate.py --mode role

# 仅测试多轮对话
python evaluate.py --mode multi

# 仅测试脱角色检测
python evaluate.py --mode escape

# 指定 LoRA 路径
python evaluate.py --model-path lora_output
```

## 已修复的问题

| 问题 | 修复方案 |
|------|----------|
| base_model_path 指向 zjy8 | 改为相对路径 models/base_model（基于项目根目录解析） |
| train_config 与 README 不同步 | 统一为 r=32/alpha=64/2epoch/dropout=0.15 |
| 缺少 LoRA 合并脚本 | post_train.py 内置 merge_lora() |
| 硬编码绝对路径 | 所有路径从 config 读取，相对路径基于 config_utils.PROJECT_ROOT 解析 |
| Modelfile 写死绝对路径 | 改为相对路径 ./gguf_output/model-Q8_0.gguf |
| Modelfile 缺少 TEMPLATE | deploy_ollama.py 真正写入 Qwen2.5 ChatML 模板 |
| 评估集与训练集重叠 | evaluate.py 用例与 eval_data.json 全部在训练语料之外独立构造 |
| perplexity 在训练集上自评 | 改用独立评估集 eval_data.json，并对误用 train_data.json 告警 |
| 依赖下界过低 | transformers 下界 4.40.0 → 4.46.0（eval_strategy / processing_class） |
| 训练不可复现 | 新增 config_utils.set_seed()，TrainingArguments 加 seed/data_seed |
| 相对路径依赖 cwd | 抽出 config_utils.load_config()/resolve_path()，三个脚本统一复用 |
| 模块顶层副作用 | post_train.py 改为 init_paths() 惰性加载配置 |
| 词表缺失伪造 pad token | 改用 tokenizer.convert_ids_to_tokens() 还原真实词表，缺失即报错退出 |
| device_map 与 model.device 冲突 | 统一用 next(model.parameters()).device 取真实设备 |
| json.load(open(...)) 句柄泄漏 | 全部改为 with 语句 |
| mirostat 与 temp/top-p/top-k 冲突 | 按是否启用 mirostat 二选一传参 |
| data_path 死配置 | config 增加 raw_data_dir/data_path，prepare_data 与 train_lora 均真正读取 |
| kb_answer_map 死代码 | 已删除 |
| GPU 策略不一致 | 检测到模型落在 CPU 时与 check_gpu() 一致，直接报错退出 |
| token 估算粗糙 | data_stats.py 优先用真实 tokenizer，否则用保守系数并标注「估算值」 |
| except 静默吞错 | 逐条记录 warning 日志 |
| 无训练日志 | 文件+控制台双输出 + history JSON + atexit flush |
| 无 EarlyStopping | 添加 EarlyStoppingCallback (patience=3) |
| 无验证集评估 | eval_strategy="steps" + eval_loss |
| 缺少拒答训练 | 添加 20 条角色保护样本 |
| 台词匹配粗糙 | 加权打分系统（权重优先级） |
| 缺少 requirements.txt | 已创建 |
| 无数据质量校验 | validate_samples() 自动检查 + 重复检测 |
| 训练过拟合 | 降低 r/alpha，增大 dropout，减少 epoch，降低学习率 |
| max_seq_length 过短 | 512 → 1024，支持多轮对话 |
| train_lora.py 代码重复 | 重构为公共函数，消除 ~100 行重复代码 |
| 评估工具用例太少 | 扩充到 21 个用例 + 3 组多轮对话 + 5 个脱角色检测 |
| 日志未正确 flush | 添加 atexit.register(logging.shutdown) |