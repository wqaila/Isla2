#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 Qwen-7B-Chat-Int4 模型转换为 GGUF 格式

此脚本用于将 HuggingFace/ModelScope 格式的 Qwen 模型转换为 llama.cpp 可使用的 GGUF 格式。

使用方法:
    python convert_to_gguf.py --model models/qwen/Qwen-7B-Chat-Int4 --output models/gguf --quantize q4_0
"""

import os
import sys
import argparse
import subprocess
import shutil
from pathlib import Path


def check_dependencies():
    """检查必要的依赖是否已安装"""
    print("检查依赖...")
    
    # 检查 Python 版本
    if sys.version_info < (3, 8):
        print("❌ 需要 Python 3.8 或更高版本")
        return False
    
    # 检查 llama.cpp 目录
    llama_cpp_path = Path("android_app/app/llama.cpp")
    if not llama_cpp_path.exists():
        print(f"❌ 找不到 llama.cpp 目录：{llama_cpp_path}")
        return False
    
    # 检查并安装必要的 Python 依赖
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
            print("   请手动运行：pip install " + ' '.join(missing_packages))
    
    print("✅ 依赖检查通过")
    return True


def convert_to_gguf(model_path: str, output_path: str, quantize: str = None):
    """
    将模型转换为 GGUF 格式
    
    Args:
        model_path: 原始模型路径
        output_path: 输出路径
        quantize: 量化级别 (q4_0, q4_1, q5_0, q5_1, q8_0 等)
    """
    model_path = Path(model_path)
    output_path = Path(output_path)
    
    # 创建输出目录
    output_path.mkdir(parents=True, exist_ok=True)
    
    llama_cpp_path = Path("android_app/app/llama.cpp")
    
    # 检查模型目录
    if not model_path.exists():
        print(f"❌ 模型目录不存在：{model_path}")
        print("请先运行 download_qwen_model.py 下载模型")
        return False
    
    # 检查是否有必要的模型文件
    required_files = ['config.json', 'modeling_qwen.py', 'tokenizer_config.json']
    for f in required_files:
        if not (model_path / f).exists():
            print(f"❌ 缺少必要的模型文件：{f}")
            return False
    
    print(f"📁 模型路径：{model_path}")
    print(f"📁 输出路径：{output_path}")
    
    # 使用 llama.cpp 的 convert_hf_to_gguf.py 脚本
    convert_script = llama_cpp_path / "convert_hf_to_gguf.py"
    
    if not convert_script.exists():
        print(f"❌ 找不到转换脚本：{convert_script}")
        return False
    
    print("🔄 开始转换模型...")
    
    # 构建命令
    cmd = [
        sys.executable,
        str(convert_script),
        str(model_path),
        "--outfile", str(output_path / "qwen-7b-chat-f16.gguf"),
    ]
    
    print(f"执行命令：{' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        print("✅ 模型转换完成")
    except subprocess.CalledProcessError as e:
        print(f"❌ 转换失败：{e}")
        return False
    
    # 如果需要量化
    if quantize:
        quantize_gguf(output_path / "qwen-7b-chat-f16.gguf", output_path, quantize)
    
    return True


def quantize_gguf(input_gguf: Path, output_dir: Path, quantize: str):
    """
    量化 GGUF 模型
    
    Args:
        input_gguf: 输入的 GGUF 文件
        output_dir: 输出目录
        quantize: 量化级别
    """
    llama_cpp_path = Path("android_app/app/llama.cpp")
    
    # 检查 quantize 工具
    quantize_tool = llama_cpp_path / "build" / "bin" / "llama-quantize"
    
    # 如果找不到 Windows 版本，尝试 Linux/macOS 版本
    if not quantize_tool.exists():
        quantize_tool = llama_cpp_path / "build" / "bin" / "llama-quantize.exe"
    if not quantize_tool.exists():
        quantize_tool = llama_cpp_path / "build" / "bin" / "quantize"
    if not quantize_tool.exists():
        # 尝试直接编译后查找
        quantize_tool = find_quantize_tool(llama_cpp_path)
    
    if not quantize_tool or not quantize_tool.exists():
        print(f"⚠️  量化工具未找到，需要先编译 llama.cpp")
        print(f"   量化后的文件将不会生成")
        print(f"   你可以稍后手动运行:")
        print(f"   {llama_cpp_path}/build/bin/llama-quantize {input_gguf} {output_dir}/qwen-7b-chat-{quantize}.gguf {quantize}")
        return False
    
    output_gguf = output_dir / f"qwen-7b-chat-{quantize}.gguf"
    
    print(f"🔄 开始量化模型：{quantize}")
    
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
        
        # 显示文件大小
        if output_gguf.exists():
            size_gb = output_gguf.stat().st_size / (1024**3)
            print(f"   文件大小：{size_gb:.2f} GB")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ 量化失败：{e}")
        return False
    
    return True


def find_quantize_tool(llama_cpp_path: Path) -> Path:
    """在 llama.cpp 目录中查找量化工具"""
    build_dirs = [
        llama_cpp_path / "build" / "bin",
        llama_cpp_path / "build" / "bin" / "Release",
        llama_cpp_path / "build" / "bin" / "Debug",
        llama_cpp_path,
    ]
    
    for build_dir in build_dirs:
        if build_dir.exists():
            for tool in build_dir.glob("*quantize*"):
                return tool
    
    return None


def build_llama_cpp():
    """编译 llama.cpp 以支持 Android"""
    llama_cpp_path = Path("android_app/app/llama.cpp")
    
    print("🔨 编译 llama.cpp...")
    
    # 创建构建目录
    build_dir = llama_cpp_path / "build"
    build_dir.mkdir(exist_ok=True)
    
    # 运行 CMake 配置
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
    
    # 运行编译
    print("编译...")
    build_cmd = ["cmake", "--build", str(build_dir), "--config", "Release", "-j"]
    
    try:
        subprocess.run(build_cmd, check=True, cwd=llama_cpp_path)
        print("✅ 编译完成")
    except subprocess.CalledProcessError as e:
        print(f"❌ 编译失败：{e}")
        return False
    
    return True


def copy_model_to_assets(gguf_path: Path, assets_dir: Path):
    """将 GGUF 模型复制到 Android assets 目录"""
    assets_dir.mkdir(parents=True, exist_ok=True)
    
    # 查找最新的 GGUF 文件
    gguf_files = list(gguf_path.glob("*.gguf"))
    
    if not gguf_files:
        print("❌ 没有找到 GGUF 模型文件")
        return False
    
    # 优先使用量化后的模型
    for q in ['q4_0', 'q4_1', 'q5_0', 'q5_1', 'q8_0', 'f16']:
        for f in gguf_files:
            if q in f.name:
                model_file = f
                break
        else:
            continue
        break
    else:
        model_file = gguf_files[0]
    
    # 复制到 assets
    dest = assets_dir / model_file.name
    print(f"📋 复制模型到 assets: {model_file.name}")
    shutil.copy2(model_file, dest)
    
    print(f"✅ 模型已复制到：{dest}")
    
    # 显示文件大小
    size_gb = dest.stat().st_size / (1024**3)
    print(f"   文件大小：{size_gb:.2f} GB")
    
    return True


def main():
    parser = argparse.ArgumentParser(description="将 Qwen 模型转换为 GGUF 格式")
    parser.add_argument("--model", "-m", default="models/qwen/Qwen-7B-Chat-Int4",
                        help="原始模型路径 (默认：models/qwen/Qwen-7B-Chat-Int4)")
    parser.add_argument("--output", "-o", default="models/gguf",
                        help="输出路径 (默认：models/gguf)")
    parser.add_argument("--quantize", "-q", default="q4_0",
                        help="量化级别：q4_0, q4_1, q5_0, q5_1, q8_0 (默认：q4_0)")
    parser.add_argument("--skip-quantize", action="store_true",
                        help="跳过量化步骤")
    parser.add_argument("--copy-assets", action="store_true",
                        help="复制模型到 Android assets 目录")
    parser.add_argument("--build", action="store_true",
                        help="先编译 llama.cpp")
    
    args = parser.parse_args()
    
    print("=" * 50)
    print("Qwen 模型转 GGUF 工具")
    print("=" * 50)
    
    # 检查依赖
    if not check_dependencies():
        sys.exit(1)
    
    # 如果需要，先编译 llama.cpp
    if args.build:
        if not build_llama_cpp():
            print("⚠️  编译失败，继续尝试转换（可能无法量化）")
    
    # 转换模型
    if not convert_to_gguf(args.model, args.output, None if args.skip_quantize else args.quantize):
        sys.exit(1)
    
    # 复制到 assets
    if args.copy_assets:
        assets_dir = Path("android_app/app/src/main/assets")
        if not copy_model_to_assets(Path(args.output), assets_dir):
            print("⚠️  复制模型到 assets 失败")
    
    print("\n" + "=" * 50)
    print("✅ 所有步骤完成!")
    print("=" * 50)
    print("\n下一步:")
    print("1. 在 Android Studio 中打开 android_app 项目")
    print("2. 同步 Gradle 文件")
    print("3. 构建并运行应用")
    print("\n如果模型文件未自动复制到 assets，请手动复制:")
    print(f"   cp {args.output}/*.gguf android_app/app/src/main/assets/")


if __name__ == "__main__":
    main()
