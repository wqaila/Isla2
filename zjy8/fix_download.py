#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复缺失的tokenizer文件（vocab.json, merges.txt, tokenizer.json）
使用 huggingface_hub 单独下载这些文件
"""

import os
import subprocess
import sys

def main():
    # 设置镜像
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    
    model_id = "Qwen/Qwen2.5-7B-Instruct"
    local_dir = "models/base_model"
    
    # 缺失的文件列表
    missing_files = ["vocab.json", "merges.txt", "tokenizer.json"]
    
    print("=" * 50)
    print("修复缺失的 tokenizer 文件")
    print("=" * 50)
    print(f"模型: {model_id}")
    print(f"目录: {local_dir}")
    print(f"缺失文件: {', '.join(missing_files)}")
    print()
    
    try:
        from huggingface_hub import hf_hub_download
        
        for fname in missing_files:
            print(f"📥 下载 {fname}...")
            try:
                hf_hub_download(
                    repo_id=model_id,
                    filename=fname,
                    local_dir=local_dir,
                    force_download=True,
                )
                print(f"  ✅ {fname} 下载完成")
            except Exception as e:
                print(f"  ❌ {fname} 下载失败: {e}")
                # 尝试 ModelScope
                print(f"  🔄 尝试从 ModelScope 下载 {fname}...")
                try:
                    pass
                    # ModelScope 不支持单文件下载，用 snapshot 但只取需要的文件
                    subprocess.check_call([
                        sys.executable, "-c",
                        f"from modelscope import snapshot_download; "
                        f"snapshot_download('Qwen/Qwen2.5-7B-Instruct', "
                        f"local_dir='{local_dir}', "
                        f"allow_file_pattern='{fname}')"
                    ])
                    print(f"  ✅ {fname} 从 ModelScope 下载完成")
                except Exception as e2:
                    print(f"  ❌ {fname} ModelScope 也失败: {e2}")
        
        print()
        print("=" * 50)
        print("修复完成!")
        print("=" * 50)
        
    except ImportError:
        print("❌ 请先安装 huggingface_hub: pip install huggingface_hub")
        sys.exit(1)

if __name__ == "__main__":
    main()
