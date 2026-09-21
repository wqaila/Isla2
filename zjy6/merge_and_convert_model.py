#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 LoRA 模型与基模型合并并转换为 GGUF 格式

此脚本用于将训练好的 LoRA 权重与基模型合并，然后转换为 llama.cpp 可使用的 GGUF 格式。
支持自由选择执行合并、GGUF 转换、量化三个步骤中的任意组合。

使用方法:
    # 执行全部流程（合并 + 转换 + 量化）
    python merge_and_convert_model.py --merge --convert --quantize q4_0

    # 仅执行合并（不转换 GGUF）
    python merge_and_convert_model.py --merge --output models/gguf/merged_model

    # 仅执行 GGUF 转换（不合并，直接转换已有模型）
    python merge_and_convert_model.py --convert --model models/gguf/merged_model/merged_model --output models/gguf

    # 仅执行量化（对已有 GGUF 文件量化）
    python merge_and_convert_model.py --quantize-only --gguf-input models/gguf/model-f16.gguf --quantize q4_0

    # 转换后量化
    python merge_and_convert_model.py --convert --model models/gguf/merged_model/merged_model --quantize q4_0

    # 全部流程 + 复制到 Android assets
    python merge_and_convert_model.py --merge --convert --quantize q4_0 --copy-assets
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path
import shutil


def check_dependencies():
    """检查必要的依赖是否已安装"""
    print("检查依赖...")
    
    # 检查 Python 版本
    if sys.version_info < (3, 8):
        print("❌ 需要 Python 3.8 或更高版本")
        return False
    
    # 检查必要的 Python 包
    required_packages = ['torch', 'transformers', 'safetensors', 'peft']
    missing_packages = []
    import_errors = {}
    
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
            print(f"  ✅ {package}")
        except ImportError:
            missing_packages.append(package)
            print(f"  ❌ {package} - 未安装")
        except OSError as e:
            # 处理 DLL 加载错误（Windows 常见问题）
            import_errors[package] = str(e)
            print(f"  ⚠️  {package} - DLL 加载失败，尝试修复...")
    
    # 如果有 DLL 错误，尝试修复
    if import_errors:
        print("\n⚠️  检测到 DLL 加载错误，尝试修复...")
        print("这通常是由于缺少 Visual C++ 运行库导致的。")
        print("\n建议操作:")
        print("1. 安装 Visual C++ Redistributable: https://aka.ms/vs/17/release/vc_redist.x64.exe")
        print("2. 或者重新安装 PyTorch:")
        print("   pip uninstall torch torchvision torchaudio")
        print("   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118")
        print("\n尝试继续运行（依赖可能仍然可用）...\n")
    
    if missing_packages:
        print(f"\n📦 安装缺失的 Python 依赖：{', '.join(missing_packages)}")
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install'] + missing_packages)
            print("✅ 依赖安装完成")
        except subprocess.CalledProcessError as e:
            print(f"⚠️  部分依赖安装失败：{e}")
            print("   请手动运行：pip install " + ' '.join(missing_packages))
    
    print("\n✅ 依赖检查完成")
    return True


def merge_lora_to_base(lora_path: Path, base_path: Path, output_path: Path):
    """
    使用 PEFT 将 LoRA 权重合并到基模型
    
    Args:
        lora_path: LoRA 模型路径
        base_path: 基模型路径
        output_path: 输出路径
    """
    print(f"\n{'='*50}")
    print("步骤 1: 合并 LoRA 权重到基模型")
    print(f"{'='*50}")
    
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 使用 Python 脚本合并
    # 注意：路径通过 sys.argv 传入，不拼接进待执行代码字符串，
    # 避免路径中带引号/反斜杠时把代码字符串拼坏（也避免代码注入）。
    merge_script = """
import sys
import os
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

base_path, lora_path, output_path = sys.argv[1], sys.argv[2], sys.argv[3]

os.environ['CUDA_VISIBLE_DEVICES'] = '-1'  # 使用 CPU

print("加载基模型...")
base_model = AutoModelForCausalLM.from_pretrained(
    base_path,
    torch_dtype=torch.float16,
    device_map="cpu",
    trust_remote_code=True
)

print("加载 LoRA 权重...")
lora_model = PeftModel.from_pretrained(
    base_model,
    lora_path,
    torch_dtype=torch.float16
)

print("合并 LoRA 权重...")
merged_model = lora_model.merge_and_unload()

print("保存合并后的模型...")
merged_model.save_pretrained(output_path, max_shard_size="5GB")

# 保存 tokenizer
tokenizer = AutoTokenizer.from_pretrained(base_path, trust_remote_code=True)
tokenizer.save_pretrained(output_path)

print("✅ 合并完成!")
"""
    
    try:
        result = subprocess.run(
            [sys.executable, '-c', merge_script,
             str(base_path), str(lora_path), str(output_path)],
            check=True,
            capture_output=True,
            text=True
        )
        if result.stdout:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ 合并失败：{e}")
        if e.stdout:
            print(f"--- stdout ---\n{e.stdout}")
        if e.stderr:
            print(f"--- stderr ---\n{e.stderr}")
        return False


def convert_to_gguf(model_path: Path, output_path: Path, quantize: str = None):
    """
    将模型转换为 GGUF 格式
    
    Args:
        model_path: 模型路径（合并后的或原始）
        output_path: 输出路径
        quantize: 量化级别
    """
    print(f"\n{'='*50}")
    print("步骤 2: 转换为 GGUF 格式")
    print(f"{'='*50}")
    
    llama_cpp_path = Path("android_app/app/llama.cpp")
    convert_script = llama_cpp_path / "convert_hf_to_gguf.py"
    
    if not convert_script.exists():
        print(f"❌ 找不到转换脚本：{convert_script}")
        return False
    
    output_path.mkdir(parents=True, exist_ok=True)
    output_gguf = output_path / "model-f16.gguf"
    
    print(f"📁 模型路径：{model_path}")
    print(f"📁 输出路径：{output_gguf}")
    
    cmd = [
        sys.executable,
        str(convert_script),
        str(model_path),
        "--outfile", str(output_gguf),
    ]
    
    print(f"执行命令：{' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        print("✅ 模型转换完成")
        
        if output_gguf.exists():
            size_gb = output_gguf.stat().st_size / (1024**3)
            print(f"   文件大小：{size_gb:.2f} GB")
    except subprocess.CalledProcessError as e:
        print(f"❌ 转换失败：{e}")
        return False
    
    # 如果需要量化
    if quantize:
        quantize_gguf(output_gguf, output_path, quantize)
    
    return True


def quantize_gguf(input_gguf: Path, output_dir: Path, quantize: str):
    """
    量化 GGUF 模型
    
    Args:
        input_gguf: 输入的 GGUF 文件
        output_dir: 输出目录
        quantize: 量化级别
    """
    print(f"\n{'='*50}")
    print(f"步骤 3: 量化模型 ({quantize})")
    print(f"{'='*50}")
    
    llama_cpp_path = Path("android_app/app/llama.cpp")
    
    # 查找量化工具
    quantize_tool = None
    possible_paths = [
        llama_cpp_path / "build" / "bin" / "llama-quantize.exe",
        llama_cpp_path / "build" / "bin" / "llama-quantize",
        llama_cpp_path / "build" / "bin" / "Release" / "llama-quantize.exe",
        llama_cpp_path / "build" / "bin" / "Release" / "llama-quantize",
    ]
    
    for path in possible_paths:
        if path.exists():
            quantize_tool = path
            break
    
    if not quantize_tool:
        print(f"⚠️  量化工具未找到，需要先编译 llama.cpp")
        print(f"   你可以手动运行:")
        print(f"   cd android_app/app/llama.cpp")
        print(f"   cmake -B build -S .")
        print(f"   cmake --build build --config Release -j")
        print(f"\n   然后手动量化:")
        print(f"   build\\bin\\llama-quantize.exe {input_gguf} {output_dir}\\model-{quantize}.gguf {quantize}")
        return False
    
    output_gguf = output_dir / f"model-{quantize}.gguf"
    
    cmd = [
        str(quantize_tool),
        str(input_gguf),
        str(output_gguf),
        quantize,
    ]
    
    print(f"执行命令：{' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        print(f"✅ 量化完成：{output_gguf}")
        
        if output_gguf.exists():
            size_gb = output_gguf.stat().st_size / (1024**3)
            print(f"   文件大小：{size_gb:.2f} GB")
    except subprocess.CalledProcessError as e:
        print(f"❌ 量化失败：{e}")
        return False
    
    return True


def copy_to_android_assets(gguf_path: Path, assets_dir: Path):
    """将 GGUF 模型复制到 Android assets 目录"""
    print(f"\n{'='*50}")
    print("步骤 4: 复制到 Android assets")
    print(f"{'='*50}")
    
    assets_dir.mkdir(parents=True, exist_ok=True)
    
    # 查找 GGUF 文件
    gguf_files = list(gguf_path.glob("*.gguf"))
    
    if not gguf_files:
        print("❌ 没有找到 GGUF 模型文件")
        return False
    
    # 优先使用量化后的模型
    selected_model = None
    for q in ['q4_0', 'q4_1', 'q5_0', 'q5_1', 'q8_0', 'f16']:
        for f in gguf_files:
            if q in f.name:
                selected_model = f
                break
        if selected_model:
            break
    
    if not selected_model:
        selected_model = gguf_files[0]
    
    # 复制到 assets
    dest = assets_dir / selected_model.name
    print(f"📋 复制模型：{selected_model.name}")
    shutil.copy2(selected_model, dest)
    
    print(f"✅ 模型已复制到：{dest}")
    
    size_gb = dest.stat().st_size / (1024**3)
    print(f"   文件大小：{size_gb:.2f} GB")
    
    return True


def build_llama_cpp():
    """编译 llama.cpp"""
    print(f"\n{'='*50}")
    print("编译 llama.cpp")
    print(f"{'='*50}")
    
    llama_cpp_path = Path("android_app/app/llama.cpp")
    build_dir = llama_cpp_path / "build"
    
    # 配置 CMake
    print("配置 CMake...")
    cmake_cmd = [
        "cmake",
        "-B", str(build_dir),
        "-S", str(llama_cpp_path),
        "-DLLAMA_BUILD_SERVER=OFF",
        "-DLLAMA_BUILD_EXAMPLES=OFF",
        "-DLLAMA_BUILD_TESTS=OFF",
    ]
    
    try:
        subprocess.run(cmake_cmd, check=True, cwd=llama_cpp_path)
        print("✅ CMake 配置完成")
    except subprocess.CalledProcessError as e:
        print(f"❌ CMake 配置失败：{e}")
        return False
    
    # 编译
    print("编译...")
    build_cmd = ["cmake", "--build", str(build_dir), "--config", "Release", "-j"]
    
    try:
        subprocess.run(build_cmd, check=True, cwd=llama_cpp_path)
        print("✅ 编译完成")
    except subprocess.CalledProcessError as e:
        print(f"❌ 编译失败：{e}")
        return False
    
    return True


def main():
    parser = argparse.ArgumentParser(
        description="合并 LoRA 模型并转换为 GGUF 格式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 全部流程：合并 LoRA + 转换 GGUF + 量化
  python merge_and_convert_model.py --merge --convert --quantize q4_0

  # 仅合并 LoRA 权重到基模型（不转换 GGUF）
  python merge_and_convert_model.py --merge --output models/gguf/merged_model

  # 仅转换已有模型为 GGUF（不合并，直接指定模型路径）
  python merge_and_convert_model.py --convert --model models/gguf/merged_model/merged_model --quantize q4_0

  # 不指定 --merge/--convert 时，默认执行全部流程（合并 + 转换）
  python merge_and_convert_model.py --quantize q4_0

  # 转换后复制到 Android assets
  python merge_and_convert_model.py --convert --model models/gguf/merged_model/merged_model --quantize q4_0 --copy-assets
        """
    )
    
    # === 步骤选择 ===
    parser.add_argument("--merge", action="store_true",
                        help="执行 LoRA 合并步骤（将 LoRA 权重合并到基模型）")
    parser.add_argument("--convert", action="store_true",
                        help="执行 GGUF 转换步骤（将模型转换为 GGUF 格式）")
    parser.add_argument("--quantize-only", action="store_true",
                        help="仅执行量化步骤（对已有 GGUF 文件进行量化，需配合 --gguf-input 使用）")
    
    # === 合并相关参数 ===
    parser.add_argument("--lora", "-l", default="models/lora_output",
                        help="LoRA 模型路径 (默认：models/lora_output)")
    parser.add_argument("--base", "-b", default="models/google/gemma-4-E2B-it",
                        help="基模型路径 (默认：models/google/gemma-4-E2B-it)")
    
    # === 转换相关参数 ===
    parser.add_argument("--model", "-m", default=None,
                        help="直接指定要转换的模型路径（仅 --convert 时使用，默认使用合并输出的模型）")
    
    # === 量化相关参数 ===
    parser.add_argument("--gguf-input", default=None,
                        help="已有 GGUF 文件路径（仅 --quantize-only 时使用）")
    parser.add_argument("--quantize", "-q", default="q4_0",
                        help="量化级别：q4_0, q4_1, q5_0, q5_1, q8_0 (默认：q4_0)")
    
    # === 通用参数 ===
    parser.add_argument("--output", "-o", default="models/gguf",
                        help="输出路径 (默认：models/gguf)")
    parser.add_argument("--skip-quantize", action="store_true",
                        help="跳过量化步骤（仅对 --convert 步骤生效）")
    parser.add_argument("--copy-assets", action="store_true",
                        help="复制模型到 Android assets 目录")
    parser.add_argument("--build", action="store_true",
                        help="先编 llama.cpp（用于量化）")
    
    args = parser.parse_args()
    
    print("=" * 50)
    print("LoRA 模型合并与 GGUF 转换工具")
    print("=" * 50)
    
    # === 仅量化模式 ===
    if args.quantize_only:
        print("📌 仅量化模式")
        
        if not args.gguf_input:
            print("❌ 仅量化模式下，必须通过 --gguf-input 指定 GGUF 文件路径")
            sys.exit(1)
        
        gguf_input = Path(args.gguf_input)
        if not gguf_input.exists():
            print(f"❌ GGUF 文件不存在：{gguf_input}")
            sys.exit(1)
        
        if not gguf_input.name.endswith('.gguf'):
            print(f"❌ 指定的文件不是 GGUF 格式：{gguf_input}")
            sys.exit(1)
        
        output_path = Path(args.output)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # 如果需要，先编译 llama.cpp
        if args.build:
            if not build_llama_cpp():
                print("⚠️  编译失败，可能无法量化")
        
        if not quantize_gguf(gguf_input, output_path, args.quantize):
            sys.exit(1)
        
        # 复制到 assets
        if args.copy_assets:
            assets_dir = Path("android_app/app/src/main/assets")
            if not copy_to_android_assets(output_path, assets_dir):
                print("⚠️  复制模型到 assets 失败")
        
        print("\n" + "=" * 50)
        print("✅ 量化完成!")
        print("=" * 50)
        print(f"\n输出文件:")
        for f in output_path.glob("*.gguf"):
            size_gb = f.stat().st_size / (1024**3)
            print(f"  - {f.name} ({size_gb:.2f} GB)")
        sys.exit(0)
    
    # === 合并/转换模式 ===
    do_merge = args.merge
    do_convert = args.convert
    if not do_merge and not do_convert:
        do_merge = True
        do_convert = True
        print("📌 未指定步骤，默认执行全部流程（合并 + 转换）")
    
    print(f"📌 执行步骤：{'合并' if do_merge else ''}{' + ' if do_merge and do_convert else ''}{'GGUF转换' if do_convert else ''}")
    
    # 根据步骤检查依赖
    if do_merge:
        if not check_dependencies():
            sys.exit(1)
    else:
        # 仅转换时，只需检查基本依赖
        print("检查依赖...")
        required_packages = ['absl-py', 'numpy', 'torch', 'transformers', 'sentencepiece', 'protobuf']
        missing_packages = []
        for package in required_packages:
            try:
                __import__(package.replace('-', '_'))
            except ImportError:
                missing_packages.append(package)
        if missing_packages:
            print(f"📦 安装缺失的 Python 依赖：{', '.join(missing_packages)}")
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install'] + missing_packages)
                print("✅ 依赖安装完成")
            except subprocess.CalledProcessError as e:
                print(f"⚠️  部分依赖安装失败：{e}")
        print("✅ 依赖检查完成")
    
    # 设置路径
    output_path = Path(args.output)
    merged_path = None
    
    # === 检查合并所需路径 ===
    if do_merge:
        lora_path = Path(args.lora)
        base_path = Path(args.base)
        
        if not lora_path.exists():
            print(f"❌ LoRA 模型路径不存在：{lora_path}")
            sys.exit(1)
        if not base_path.exists():
            print(f"❌ 基模型路径不存在：{base_path}")
            sys.exit(1)
    
    # === 检查转换所需路径 ===
    if do_convert and not do_merge:
        # 仅转换模式：必须指定 --model 参数
        if args.model:
            merged_path = Path(args.model)
        else:
            # 尝试使用默认的合并输出路径
            default_merged = output_path / "merged_model" / "merged_model"
            if default_merged.exists():
                merged_path = default_merged
                print(f"📌 使用默认合并模型路径：{merged_path}")
            else:
                print(f"❌ 仅转换模式下，必须通过 --model 指定模型路径")
                print(f"   或确保默认路径存在：{default_merged}")
                sys.exit(1)
        
        if not merged_path.exists():
            print(f"❌ 模型路径不存在：{merged_path}")
            sys.exit(1)
    
    # 如果需要，先编译 llama.cpp
    if args.build:
        if not build_llama_cpp():
            print("⚠️  编译失败，继续尝试转换（可能无法量化）")
    
    # === 步骤 1: 合并 LoRA ===
    if do_merge:
        merge_output_path = output_path / "merged_model"
        if not merge_lora_to_base(lora_path, base_path, merge_output_path):
            if do_convert:
                print("⚠️  合并失败，尝试直接使用基模型转换")
                merged_path = base_path
            else:
                print("❌ 合并失败")
                sys.exit(1)
        else:
            merged_path = merge_output_path
    
    # === 步骤 2: 转换为 GGUF ===
    if do_convert:
        if merged_path is None:
            print(f"❌ 无法确定要转换的模型路径")
            sys.exit(1)
        
        if not convert_to_gguf(merged_path, output_path, None if args.skip_quantize else args.quantize):
            sys.exit(1)
    
    # 复制到 assets
    if args.copy_assets:
        assets_dir = Path("android_app/app/src/main/assets")
        if not copy_to_android_assets(output_path, assets_dir):
            print("⚠️  复制模型到 assets 失败")
    
    print("\n" + "=" * 50)
    print("✅ 所有步骤完成!")
    print("=" * 50)
    print(f"\n输出文件:")
    for f in output_path.glob("*.gguf"):
        size_gb = f.stat().st_size / (1024**3)
        print(f"  - {f.name} ({size_gb:.2f} GB)")
    print("\n下一步:")
    print("1. Android 部署：在 Android Studio 中打开 android_app 项目，同步并构建")
    print("2. 把生成的 GGUF 文件推送到手机外部存储（如 adb push xxx.gguf /sdcard/）")


if __name__ == "__main__":
    main()
