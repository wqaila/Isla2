#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载 Qwen2.5-7B-Instruct 基模型（HuggingFace 格式）

使用国内镜像站加速下载，用于后续 LoRA 微调。

使用方法:
    python download_model.py                          # 默认下载到 models/base_model
    python download_model.py --output models/my_model # 指定输出目录
    python download_model.py --model Qwen/Qwen2.5-14B-Instruct  # 下载其他模型
    python download_model.py --mirror modelscope       # 使用 ModelScope 镜像
    python download_model.py --resume                  # 断点续传（默认已开启）
    python download_model.py --no-resume               # 强制重新下载
"""

import os
import sys
import argparse
from pathlib import Path


# 国内镜像站列表
MIRRORS = {
    "hf-mirror": "https://hf-mirror.com",
    "huggingface": "https://huggingface.co",
}


def check_dependencies():
    """检查依赖是否可用；缺失时给出安装指引（不自动安装，避免悄悄改动用户的 Python 环境）"""
    required = ["huggingface_hub"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"❌ 缺少依赖: {', '.join(missing)}")
        print("   请先手动安装（本脚本不会自动执行 pip install）：")
        print(f"   {Path(sys.executable).name} -m pip install {' '.join(missing)}")
        print("   国内网络较慢可加镜像：-i https://pypi.tuna.tsinghua.edu.cn/simple")
        return False
    return True


def download_with_hf_hub(
    model_id: str,
    output_dir: str,
    mirror: str = "hf-mirror",
    resume: bool = True,
    token: str = None,
):
    """
    使用 huggingface_hub 下载模型

    Args:
        model_id: HuggingFace 模型 ID
        output_dir: 输出目录
        mirror: 镜像站名称
        resume: 是否断点续传
        token: HuggingFace token（可选，部分模型需要）
    """
    from huggingface_hub import snapshot_download

    # 设置镜像站环境变量
    if mirror != "huggingface":
        mirror_url = MIRRORS.get(mirror, mirror)
        os.environ["HF_ENDPOINT"] = mirror_url
        print(f"🌐 使用镜像站: {mirror_url}")
    else:
        print(f"🌐 使用官方站: huggingface.co")

    print(f"📥 模型: {model_id}")
    print(f"📁 输出: {output_dir}")
    print(f"🔄 断点续传: {'是' if resume else '否（强制重新下载）'}")
    print()

    try:
        # 说明：huggingface_hub 新版本里 resume_download / local_dir_use_symlinks 已废弃。
        # 新版默认就是「断点续传 + 直接落地文件（不用软链）」，所以这两个参数已移除；
        # 需要重新下载时用 force_download=True 即可。
        snapshot_download(
            repo_id=model_id,
            local_dir=output_dir,
            token=token,
            force_download=not resume,
        )
        return True
    except Exception as e:
        print(f"\n❌ 下载失败: {e}")
        return False


def download_with_modelscope(
    model_id: str,
    output_dir: str,
    resume: bool = True,
):
    """
    使用 ModelScope 下载模型（备选方案）

    Args:
        model_id: 模型 ID
        output_dir: 输出目录
        resume: 是否断点续传（ModelScope 默认即断点续传，这里仅用于提示）
    """
    # Qwen2.5-7B-Instruct 在 ModelScope 上的 ID
    ms_model_map = {
        "Qwen/Qwen2.5-7B-Instruct": "Qwen/Qwen2.5-7B-Instruct",
        "Qwen/Qwen2.5-14B-Instruct": "Qwen/Qwen2.5-14B-Instruct",
        "Qwen/Qwen2.5-3B-Instruct": "Qwen/Qwen2.5-3B-Instruct",
    }

    ms_model_id = ms_model_map.get(model_id, model_id)

    try:
        from modelscope import snapshot_download as ms_download
    except ImportError:
        print("❌ 未安装 modelscope，无法使用 ModelScope 镜像。")
        print("   请先手动安装（本脚本不会自动执行 pip install）：")
        print(f"   {Path(sys.executable).name} -m pip install modelscope")
        return False

    print(f"🌐 使用 ModelScope 镜像")
    print(f"📥 模型: {ms_model_id}")
    print(f"📁 输出: {output_dir}")
    print()

    try:
        ms_download(
            ms_model_id,
            local_dir=output_dir,
        )
        return True
    except Exception as e:
        print(f"\n❌ 下载失败: {e}")
        return False


def verify_download(output_dir: str) -> bool:
    """验证下载是否完整"""
    output_path = Path(output_dir)

    print(f"\n{'='*50}")
    print("验证下载结果")
    print(f"{'='*50}")

    # 检查必要文件
    required_files = ["config.json", "tokenizer.json", "tokenizer_config.json"]
    model_files = list(output_path.glob("model*.safetensors")) + list(
        output_path.glob("model*.bin")
    )

    all_ok = True

    # 检查配置文件
    for f in required_files:
        fp = output_path / f
        if fp.exists():
            print(f"  ✅ {f}")
        else:
            print(f"  ❌ {f} - 缺失")
            all_ok = False

    # 检查模型权重
    if model_files:
        total_size = sum(f.stat().st_size for f in model_files) / (1024**3)
        print(f"  ✅ 模型权重: {len(model_files)} 个文件, 共 {total_size:.2f} GB")
    else:
        print(f"  ❌ 模型权重文件缺失")
        all_ok = False

    if all_ok:
        print(f"\n✅ 下载验证通过! 模型保存在: {output_path}")
    else:
        print(f"\n⚠️  下载可能不完整，请检查或重新下载")

    return all_ok


def main():
    parser = argparse.ArgumentParser(
        description="下载 Qwen2.5-7B-Instruct 基模型（国内镜像加速）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 默认下载 Qwen2.5-7B-Instruct（使用 hf-mirror 镜像）
    python download_model.py

    # 使用 ModelScope 下载（国内最快）
    python download_model.py --mirror modelscope

    # 下载 14B 模型
    python download_model.py --model Qwen/Qwen2.5-14B-Instruct

    # 指定输出目录
    python download_model.py --output models/qwen2.5-7b

    # 断点续传（默认开启）
    python download_model.py --resume

    # 强制重新下载（忽略已有文件）
    python download_model.py --no-resume

镜像站说明:
    hf-mirror    - HuggingFace 国内镜像 (https://hf-mirror.com) [默认]
    huggingface  - HuggingFace 官方站 (需要科学上网)
    modelscope   - 阿里 ModelScope 镜像 (国内最快，但模型可能更新较慢)
        """,
    )

    parser.add_argument(
        "--model",
        "-m",
        default="Qwen/Qwen2.5-7B-Instruct",
        help="HuggingFace 模型 ID (默认: Qwen/Qwen2.5-7B-Instruct)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="models/base_model",
        help="输出目录 (默认: models/base_model)",
    )
    parser.add_argument(
        "--mirror",
        choices=["hf-mirror", "huggingface", "modelscope"],
        default="hf-mirror",
        help="镜像站选择 (默认: hf-mirror)",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="断点续传（默认开启；用 --no-resume 强制重新下载）",
    )
    parser.add_argument(
        "--token",
        "-t",
        default=None,
        help="HuggingFace token（部分模型需要登录才能下载）",
    )

    args = parser.parse_args()

    print("=" * 50)
    print("LLM 基模型下载工具")
    print("=" * 50)
    print(f"模型: {args.model}")
    print(f"镜像: {args.mirror}")
    print(f"输出: {args.output}")
    print()

    # 检查依赖（只检测，不自动安装）
    print("检查依赖...")
    if not check_dependencies():
        sys.exit(1)
    print()

    # 检查是否已存在
    output_path = Path(args.output)
    if output_path.exists() and list(output_path.glob("*.safetensors")):
        print(f"⚠️  目标目录已存在且包含模型文件: {args.output}")
        choice = input("是否继续下载（断点续传）？[y/N]: ").strip().lower()
        if choice != "y":
            print("已取消下载")
            sys.exit(0)
        print()

    # 创建输出目录
    output_path.mkdir(parents=True, exist_ok=True)

    # 执行下载
    resume = args.resume

    if args.mirror == "modelscope":
        success = download_with_modelscope(args.model, args.output, resume)
    else:
        success = download_with_hf_hub(
            args.model, args.output, args.mirror, resume, args.token
        )

    if success:
        verify_download(args.output)
        print(f"\n{'='*50}")
        print("✅ 下载完成!")
        print(f"{'='*50}")
        print(f"\n下一步:")
        print(f"  1. 准备语料库数据 (JSON 格式)")
        print(f"  2. 运行 LoRA 训练脚本")
        print(f"  3. 合并模型并转换为 GGUF")
    else:
        print(f"\n{'='*50}")
        print("❌ 下载失败!")
        print(f"{'='*50}")
        print(f"\n尝试其他镜像:")
        print(f"  python download_model.py --mirror modelscope")
        print(f"  python download_model.py --mirror huggingface --token YOUR_TOKEN")
        sys.exit(1)


if __name__ == "__main__":
    main()
