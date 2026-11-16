"""验证配置项清理后的一致性。"""
from __future__ import annotations

import sys

sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

from custom_voice.config.schema import CONSUMED_FIELDS, GPUConfig

model_fields = set(GPUConfig.model_fields)
consumed = set(CONSUMED_FIELDS)

print(f"模型字段数: {len(model_fields)}")
print(f"登记字段数: {len(consumed)}")
print(f"差异: {model_fields ^ consumed or '无'}")

print("\n所有字段:")
for f in sorted(model_fields):
    mark = "OK" if f in consumed else "未登记"
    print(f"  {f:<28}{mark}")

# 检查已删除的字段确实不在
removed = ["enable_sdpa", "enable_vllm_llm", "cpu_offload"]
print("\n已删除字段检查:")
for r in removed:
    print(f"  {r:<20}{'仍存在（错误）' if r in model_fields else '已删除 OK'}")

sys.exit(0 if model_fields == consumed else 1)
