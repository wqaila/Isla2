"""验证配置加载与 compile 实现。"""
from __future__ import annotations

import sys

sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

from custom_voice.config.loader import load_config
from custom_voice.config.schema import CONSUMED_FIELDS, GPUConfig

cfg = load_config()
print(f"配置加载成功，字段数 {len(cfg.model_fields)}")
print(f"enable_compile = {cfg.enable_compile}")
print(f"stream_enabled = {cfg.stream_enabled}")
print(f"stream_char_threshold = {cfg.stream_char_threshold}")
print(f"max_queue_size = {cfg.max_queue_size}")

# 确认已删除字段不在
removed = ["enable_sdpa", "enable_vllm_llm", "cpu_offload"]
bad = [r for r in removed if r in cfg.model_fields]
print(f"\n已删除字段残留: {bad or '无'}")

# 确认登记表与字段一致
diff = set(cfg.model_fields) ^ set(CONSUMED_FIELDS)
print(f"登记表差异: {diff or '无'}")

# 确认 compile 的消费方是真实模块
print(f"\nenable_compile 消费方: {CONSUMED_FIELDS['enable_compile']}")

sys.exit(0 if not bad and not diff else 1)
