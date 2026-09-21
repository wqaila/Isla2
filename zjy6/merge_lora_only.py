#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 LoRA 权重合并到基模型

此脚本专门用于将训练好的 LoRA 权重与基模型合并，保存为独立的 HuggingFace 模型。

使用方法:
    python merge_lora_only.py --lora models/lora_output --base models/google/gemma-4-E2B-it --output models/gguf/merged_model
"""

import os
import sys
import argparse
import torch
from pathlib import Path
from transformers import AutoModelForCausalLM, AutoTokenizer, AutoConfig
from peft import PeftModel


def merge_lora_to_base(lora_path: Path, base_path: Path, output_path: Path, dtype: str = "float16"):
    """
    将 LoRA 权重合并到基模型
    
    Args:
        lora_path: LoRA 模型路径
        base_path: 基模型路径
        output_path: 输出路径
        dtype: 数据类型 (float16, bfloat16, float32)
    """
    # 解析数据类型
    dtype_map = {
        "float16": torch.float16,
        "bfloat16": torch.bfloat16,
        "float32": torch.float32
    }
    torch_dtype = dtype_map.get(dtype, torch.float16)
    
    # 强制使用 CPU
    os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
    
    print("=" * 50)
    print("LoRA 权重合并工具")
    print("=" * 50)
    
    # 检查路径
    if not lora_path.exists():
        print(f"❌ LoRA 模型路径不存在：{lora_path}")
        return False
    
    if not base_path.exists():
        print(f"❌ 基模型路径不存在：{base_path}")
        return False
    
    # 创建输出目录
    output_path.mkdir(parents=True, exist_ok=True)
    
    try:
        # 加载基模型配置
        print(f"\n📋 加载基模型配置：{base_path}")
        config = AutoConfig.from_pretrained(
            base_path,
            trust_remote_code=True
        )
        print(f"   模型类型：{config.model_type}")
        
        # 加载基模型
        print(f"\n🏗️  加载基模型...")
        base_model = AutoModelForCausalLM.from_pretrained(
            base_path,
            torch_dtype=torch_dtype,
            device_map="cpu",
            trust_remote_code=True
        )
        print("   ✅ 基模型加载完成")
        
        # 加载 LoRA 权重
        print(f"\n🔧 加载 LoRA 权重：{lora_path}")
        lora_model = PeftModel.from_pretrained(
            base_model,
            lora_path,
            torch_dtype=torch_dtype
        )
        print("   ✅ LoRA 权重加载完成")
        
        # 合并 LoRA 权重
        print(f"\n🔗 合并 LoRA 权重到基模型...")
        merged_model = lora_model.merge_and_unload()
        print("   ✅ 合并完成!")
        
        # 保存合并后的模型（使用 torch.save 绕过 safetensors 大张量限制）
        print(f"\n💾 保存合并后的模型到：{output_path}")
        output_path.mkdir(parents=True, exist_ok=True)
        
        # 获取模型状态字典
        state_dict = merged_model.state_dict()
        
        # 按 2GB 分片保存
        shard_size_limit = 2 * 1024 * 1024 * 1024  # 2GB
        shards = []            # 每个元素为 {参数名: tensor}
        current_shard = {}
        current_shard_size = 0
        
        for key, tensor in state_dict.items():
            tensor_size = tensor.nelement() * tensor.element_size()
            if current_shard_size + tensor_size > shard_size_limit and current_shard:
                shards.append(current_shard)
                current_shard = {}
                current_shard_size = 0
            
            current_shard[key] = tensor
            current_shard_size += tensor_size
        
        # 保存最后一个分片
        if current_shard:
            shards.append(current_shard)
        
        # 分片总数确定后再生成文件名，保证 -of-{total} 与实际分片数一致，
        # 否则多分片时 HuggingFace 会因为总数错误而无法加载。
        total_shards = len(shards)
        weight_map = {}
        for idx, shard in enumerate(shards):
            shard_file = f"pytorch_model-{idx + 1:05d}-of-{total_shards:05d}.bin"
            shard_path = output_path / shard_file
            print(f"   保存分片：{shard_file}")
            torch.save(shard, shard_path)
            for k in shard:
                weight_map[k] = shard_file
        
        # 保存 index 文件
        import json
        index = {
            "metadata": {"total_size": sum(t.nelement() * t.element_size() for t in state_dict.values())},
            "weight_map": weight_map
        }
        index_path = output_path / "pytorch_model.bin.index.json"
        with open(index_path, 'w') as f:
            json.dump(index, f, indent=2)
        
        print(f"   ✅ 模型权重保存完成（共 {total_shards} 个分片）")
        
        # 保存配置
        config.save_pretrained(output_path)
        print("   ✅ 配置文件保存完成")
        
        # 保存 tokenizer
        print(f"\n📝 保存 tokenizer...")
        try:
            tokenizer = AutoTokenizer.from_pretrained(base_path, trust_remote_code=True)
            tokenizer.save_pretrained(output_path)
            print("   ✅ Tokenizer 保存完成")
        except Exception as e:
            print(f"   ⚠️  Tokenizer 保存失败：{e}")
            print("   请手动从基模型复制 tokenizer 文件")
        
        print("\n" + "=" * 50)
        print("✅ 合并完成!")
        print("=" * 50)
        print(f"\n输出目录：{output_path}")
        
        # 显示输出文件
        print("\n生成的文件:")
        for f in output_path.iterdir():
            if f.is_file():
                size_mb = f.stat().st_size / (1024 * 1024)
                print(f"  - {f.name} ({size_mb:.2f} MB)")
            elif f.is_dir():
                size_mb = sum(f.stat().st_size for f in f.rglob('*') if f.is_file()) / (1024 * 1024)
                print(f"  - {f.name}/ ({size_mb:.2f} MB)")
        
        return True
        
    except KeyError as e:
        print(f"\n❌ 模型类型不支持：{e}")
        print("\n这可能是因为 transformers 库版本过旧，不支持该模型类型。")
        print("\n解决方案:")
        print("1. 更新 transformers 到最新版本:")
        print("   pip install --upgrade transformers")
        print("\n2. 或者从源码安装 transformers:")
        print("   pip install git+https://github.com/huggingface/transformers.git")
        print("\n3. 检查模型是否兼容:")
        print(f"   模型路径：{base_path}")
        print(f"   模型类型：{config.model_type if 'config' in locals() else '未知'}")
        return False
        
    except Exception as e:
        print(f"\n❌ 合并失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(
        description="将 LoRA 权重合并到基模型",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 基本用法
    python merge_lora_only.py --lora models/lora_output --base models/google/gemma-4-E2B-it --output models/gguf/merged_model
    
    # 使用 float32 精度
    python merge_lora_only.py --lora models/lora_output --base models/google/gemma-4-E2B-it --output models/gguf/merged_model --dtype float32
    
    # 使用 bfloat16 精度
    python merge_lora_only.py --lora models/lora_output --base models/google/gemma-4-E2B-it --output models/gguf/merged_model --dtype bfloat16
        """
    )
    parser.add_argument(
        "--lora", "-l", 
        default="models/lora_output",
        help="LoRA 模型路径 (默认：models/lora_output)"
    )
    parser.add_argument(
        "--base", "-b", 
        default="models/google/gemma-4-E2B-it",
        help="基模型路径 (默认：models/google/gemma-4-E2B-it)"
    )
    parser.add_argument(
        "--output", "-o", 
        default="models/gguf/merged_model",
        help="输出路径 (默认：models/gguf/merged_model)"
    )
    parser.add_argument(
        "--dtype", "-d", 
        choices=["float16", "bfloat16", "float32"],
        default="float16",
        help="数据类型 (默认：float16)"
    )
    
    args = parser.parse_args()
    
    success = merge_lora_to_base(
        Path(args.lora),
        Path(args.base),
        Path(args.output),
        args.dtype
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
