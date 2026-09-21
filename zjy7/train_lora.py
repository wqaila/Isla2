#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Qwen2 LoRA 微调训练（重构版：消除重复代码）"""

import os, sys, json, logging, torch
from pathlib import Path
from datetime import datetime
from transformers import (
    AutoTokenizer, AutoModelForCausalLM, TrainingArguments, Trainer,
    DataCollatorForSeq2Seq, BitsAndBytesConfig, EarlyStoppingCallback
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training, TaskType
from datasets import Dataset

from config_utils import load_config, resolve_path, set_seed

# 全局随机种子（保证训练可复现）
SEED = 42

# ============ 日志配置 ============
def setup_logging(output_dir):
    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, f"train_log_{datetime.now():%Y%m%d_%H%M%S}.txt")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.info(f"日志文件: {log_file}")
    return log_file

# ============ 自定义 Trainer（记录 loss） ============
class LoggingTrainer(Trainer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.train_losses = []
        self.eval_losses = []

    def log(self, logs, *args, **kwargs):
        super().log(logs, *args, **kwargs)
        if "loss" in logs:
            self.train_losses.append({"step": self.state.global_step, "loss": logs["loss"]})
        if "eval_loss" in logs:
            self.eval_losses.append({"step": self.state.global_step, "eval_loss": logs["eval_loss"]})

    def save_training_history(self, output_dir):
        history_path = os.path.join(output_dir, "training_history.json")
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump({"train_losses": self.train_losses, "eval_losses": self.eval_losses}, f, ensure_ascii=False, indent=2)
        logging.info(f"训练历史已保存: {history_path}")

# ============ 数据加载 ============
def load_data(path, tokenizer, max_len):
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    logging.info(f"加载 {len(raw)} 条样本")

    ids, masks, labels = [], [], []
    skipped = 0
    for idx, item in enumerate(raw):
        msgs = item["messages"]
        try:
            full = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
            prompt_msgs = [m for m in msgs if m["role"] != "assistant"]
            prompt = tokenizer.apply_chat_template(prompt_msgs, tokenize=False, add_generation_prompt=True)
        except Exception as e:
            logging.warning(f"样本 {idx} apply_chat_template 失败: {e}")
            skipped += 1
            continue

        full_enc = tokenizer(full, truncation=True, max_length=max_len, padding=False)
        prompt_enc = tokenizer(prompt, truncation=True, max_length=max_len, padding=False)
        input_ids = full_enc["input_ids"]

        if len(input_ids) < 10:
            logging.debug(f"样本 {idx} 过短 ({len(input_ids)} tokens)，跳过")
            skipped += 1
            continue

        lbl = input_ids.copy()
        for j in range(min(len(prompt_enc["input_ids"]), len(lbl))):
            lbl[j] = -100
        ids.append(input_ids)
        masks.append(full_enc["attention_mask"])
        labels.append(lbl)

    logging.info(f"有效样本: {len(ids)}, 跳过: {skipped}")
    return Dataset.from_dict({"input_ids": ids, "attention_mask": masks, "labels": labels})

# ============ 构建模型 ============
def build_model(model_path, lora_r, lora_alpha, lora_dropout):
    """加载 4-bit 量化模型并附加 LoRA"""
    logging.info("加载模型 (4-bit NF4 量化)...")
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_path, trust_remote_code=True,
        quantization_config=bnb, device_map={"": 0}, torch_dtype=torch.bfloat16
    )
    model = prepare_model_for_kbit_training(model)

    device = next(model.parameters()).device
    logging.info(f"模型设备: {device}")
    if device.type == "cpu":
        # 与 check_gpu() 保持一致：CPU 训练不可用，直接报错退出，避免误跑数小时
        logging.error("模型被加载到 CPU 上！请检查 CUDA / bitsandbytes 配置。")
        sys.exit(1)

    lora = LoraConfig(
        r=lora_r, lora_alpha=lora_alpha, lora_dropout=lora_dropout,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        bias="none", task_type=TaskType.CAUSAL_LM
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()
    return model

# ============ 构建训练参数 ============
def build_training_args(cfg, eval_ds):
    tcfg = cfg["training"]
    return TrainingArguments(
        output_dir=cfg["output"]["output_dir"],
        num_train_epochs=tcfg["num_train_epochs"],
        per_device_train_batch_size=tcfg["per_device_train_batch_size"],
        gradient_accumulation_steps=tcfg["gradient_accumulation_steps"],
        learning_rate=tcfg["learning_rate"],
        warmup_ratio=tcfg["warmup_ratio"],
        logging_steps=tcfg["logging_steps"],
        save_steps=tcfg["save_steps"],
        save_total_limit=3,
        bf16=tcfg["bf16"],
        gradient_checkpointing=tcfg["gradient_checkpointing"],
        weight_decay=tcfg["weight_decay"],
        max_grad_norm=tcfg["max_grad_norm"],
        eval_strategy="steps" if eval_ds else "no",
        eval_steps=tcfg["save_steps"] if eval_ds else None,
        load_best_model_at_end=True if eval_ds else False,
        metric_for_best_model="eval_loss" if eval_ds else None,
        greater_is_better=False,
        report_to="none",
        remove_unused_columns=False,
        dataloader_pin_memory=False,
        lr_scheduler_type="cosine",
        seed=SEED,
        data_seed=SEED
    )

# ============ 构建数据集 ============
def build_datasets(tokenizer, max_seq_length, data_path):
    dataset = load_data(data_path, tokenizer, max_seq_length)
    if len(dataset) > 20:
        split = dataset.train_test_split(test_size=0.05, seed=SEED)
        train_ds, eval_ds = split["train"], split["test"]
    else:
        train_ds, eval_ds = dataset, None
        logging.warning("样本数 <= 20，跳过验证集划分")
    logging.info(f"训练集: {len(train_ds)}, 验证集: {len(eval_ds) if eval_ds else 0}")
    return train_ds, eval_ds

# ============ 构建 Trainer ============
def build_trainer(model, tokenizer, train_ds, eval_ds, args, max_seq_length, patience):
    callbacks = []
    if eval_ds is not None and patience > 0:
        callbacks.append(EarlyStoppingCallback(early_stopping_patience=patience))
        logging.info(f"EarlyStopping patience={patience}")

    return LoggingTrainer(
        model=model, args=args,
        train_dataset=train_ds, eval_dataset=eval_ds,
        data_collator=DataCollatorForSeq2Seq(
            tokenizer=tokenizer, padding=True,
            max_length=max_seq_length, return_tensors="pt"
        ),
        processing_class=tokenizer,
        callbacks=callbacks
    )

# ============ GPU 验证 ============
def check_gpu():
    os.environ["CUDA_VISIBLE_DEVICES"] = "0"
    if not torch.cuda.is_available():
        logging.error("CUDA 不可用！请检查 GPU 驱动和 PyTorch CUDA 版本")
        sys.exit(1)
    gpu_props = torch.cuda.get_device_properties(0)
    logging.info(f"GPU: {torch.cuda.get_device_name(0)}, 显存: {gpu_props.total_memory / 1024**3:.1f}GB")

# ============ 主流程 ============
def main(resume_path=None):
    set_seed(SEED)
    check_gpu()
    cfg = load_config()
    mcfg, dcfg, tcfg = cfg["model"], cfg["data"], cfg["training"]
    model_path = str(resolve_path(mcfg["base_model_path"]))
    if not os.path.isdir(model_path):
        logging.error(f"基础模型路径不存在: {model_path}")
        sys.exit(1)
    output_dir = str(resolve_path(cfg["output"]["output_dir"]))
    data_path = str(resolve_path(dcfg["data_path"]))
    if not os.path.isfile(data_path):
        logging.error(f"训练数据文件不存在: {data_path}（请先运行 prepare_data.py）")
        sys.exit(1)

    setup_logging(output_dir)
    if resume_path:
        logging.info(f"断点续训，从 {resume_path} 恢复")
    logging.info(f"配置: {json.dumps(cfg, ensure_ascii=False, indent=2)}")

    # 加载 tokenizer
    logging.info(f"加载 tokenizer: {model_path}")
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True, padding_side="right")
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        logging.info("pad_token 设为 eos_token")

    # 构建模型
    model = build_model(model_path, mcfg["lora_r"], mcfg["lora_alpha"], mcfg["lora_dropout"])

    # 加载数据
    train_ds, eval_ds = build_datasets(tokenizer, dcfg["max_seq_length"], data_path)

    # 训练参数
    args = build_training_args(cfg, eval_ds)

    # Trainer
    trainer = build_trainer(
        model, tokenizer, train_ds, eval_ds, args,
        dcfg["max_seq_length"], tcfg.get("early_stopping_patience", 3)
    )

    # 开始训练
    logging.info("开始训练..." if not resume_path else "从 checkpoint 恢复训练...")
    trainer.train(resume_from_checkpoint=resume_path)

    # 保存训练历史和模型
    trainer.save_training_history(output_dir)
    logging.info(f"保存模型到: {output_dir}")
    trainer.save_model(output_dir)
    tokenizer.save_pretrained(output_dir)
    logging.info("训练完成！")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Qwen2 LoRA 微调训练")
    parser.add_argument("--resume", type=str, nargs="?", const="latest",
                        help="断点续训。不指定路径则自动查找最新 checkpoint")
    args = parser.parse_args()

    if args.resume:
        if args.resume == "latest":
            cfg = load_config()
            output_dir = resolve_path(cfg["output"]["output_dir"])
            checkpoints = sorted(Path(output_dir).glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[-1]))
            if checkpoints:
                resume_path = str(checkpoints[-1])
                logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
                logging.info(f"自动恢复 checkpoint: {resume_path}")
            else:
                print("未找到 checkpoint，从头开始训练")
                resume_path = None
        else:
            resume_path = args.resume
        main(resume_path=resume_path)
    else:
        main()