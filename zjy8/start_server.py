#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动本地 LLM 推理服务

使用 llama.cpp 的 llama-server 提供 OpenAI 兼容的 API 接口。
手机端可通过网络调用此服务进行推理。

安全提示（重要）：
    默认只监听 127.0.0.1（仅本机可访问）。如果要让局域网内的手机直接访问，
    必须显式指定 --host 0.0.0.0，此时**强制要求**提供 --api-key，
    否则服务会以无认证方式暴露给整个局域网，任何人都能白嫖你的 GPU。

使用方法:
    # 使用默认模型启动（自动查找 GGUF 文件）
    python start_server.py

    # 指定模型文件
    python start_server.py --model path/to/model.gguf

    # 指定端口和 GPU 层数
    python start_server.py --port 8080 --gpu-layers 20

    # 仅 CPU 推理（无 GPU 时，也是默认值）
    python start_server.py --gpu-layers 0

    # 允许局域网访问（必须带 api-key）
    python start_server.py --host 0.0.0.0 --api-key <你的密钥>
"""

import os
import sys
import argparse
import socket
import subprocess
from pathlib import Path

# 本脚本所在目录，所有默认路径都基于它推导，避免写死绝对路径
PROJECT_ROOT = Path(__file__).resolve().parent

# 环境变量：额外的模型搜索目录（多个目录用 os.pathsep 分隔）
ENV_MODEL_DIRS = "ZJY8_MODEL_DIRS"
# 环境变量：llama-server 可执行文件路径
ENV_LLAMA_SERVER = "ZJY8_LLAMA_SERVER"
# 环境变量：API 密钥
ENV_API_KEY = "ZJY8_API_KEY"

# 视为「仅本机」的监听地址
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _env_path_list(name):
    """把环境变量里的多个路径拆成 Path 列表"""
    raw = os.environ.get(name, "")
    if not raw:
        return []
    return [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]


def default_model_dirs():
    """默认的 GGUF 搜索目录（基于本脚本所在目录推导）"""
    return [
        PROJECT_ROOT,
        PROJECT_ROOT / "models" / "gguf",
        PROJECT_ROOT / "models",
    ]


def find_gguf_model(search_dirs=None):
    """自动查找可用的 GGUF 模型文件"""
    if search_dirs is None:
        search_dirs = default_model_dirs() + _env_path_list(ENV_MODEL_DIRS)

    # 优先级：量化模型 > F16模型
    priority = ["q5_k_m", "q5_k_s", "q4_k_m", "q4_1", "q4_0", "q8_0", "f16"]

    all_gguf = []
    for d in search_dirs:
        if d.is_dir():
            all_gguf.extend(d.glob("*.gguf"))

    if not all_gguf:
        return None

    # 按优先级排序
    def sort_key(f):
        name = f.name.lower()
        for i, p in enumerate(priority):
            if p in name:
                return i
        return len(priority)

    all_gguf.sort(key=sort_key)
    return all_gguf[0]


def default_server_paths():
    """llama-server 的候选路径（基于本脚本所在目录推导，不再依赖 ../zjy6）"""
    return [
        # 本目录自带的 Windows CPU 版 llama.cpp 发布包
        PROJECT_ROOT / "models" / "llama-b9222-bin-win-cpu-x64" / "llama-server.exe",
        # 常见的本地编译产物
        PROJECT_ROOT / "llama.cpp" / "build" / "bin" / "llama-server.exe",
        PROJECT_ROOT / "llama.cpp" / "build" / "bin" / "Release" / "llama-server.exe",
        PROJECT_ROOT / "llama.cpp" / "build" / "bin" / "llama-server",
    ]


def find_llama_server(extra_dirs=None):
    """查找 llama-server 可执行文件"""
    # 1. 环境变量显式指定
    env_path = os.environ.get(ENV_LLAMA_SERVER, "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists():
            return p

    # 2. 命令行 --llama-dir 指定的目录
    candidates = []
    for d in extra_dirs or []:
        candidates.append(d / "llama-server.exe")
        candidates.append(d / "llama-server")

    # 3. 默认候选路径
    candidates.extend(default_server_paths())
    for p in candidates:
        if p.exists():
            return p

    # 4. 尝试在 PATH 中查找
    try:
        result = subprocess.run(
            ["where", "llama-server"] if os.name == "nt" else ["which", "llama-server"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            first = result.stdout.strip().splitlines()
            if first:
                return Path(first[0])
    except (OSError, subprocess.SubprocessError) as e:
        print(f"⚠️  在 PATH 中查找 llama-server 失败: {e}")

    return None


def get_local_ip():
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError as e:
        print(f"⚠️  获取本机 IP 失败（不影响本机使用）: {e}")
        return "127.0.0.1"


def mask_key(key):
    """打印密钥时只显示首尾，避免整串密钥出现在日志里"""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * 8}{key[-4:]}"


def build_parser():
    parser = argparse.ArgumentParser(
        description="启动本地 LLM 推理服务",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
示例:
    # 自动查找模型并启动（仅本机可访问）
    python start_server.py

    # 指定模型
    python start_server.py --model models/gguf/model-q5_k_m.gguf

    # GPU 加速（层数按显存调整：RTX 3060 12GB 可参考 20~35 层）
    python start_server.py --gpu-layers 35

    # 仅 CPU 推理
    python start_server.py --gpu-layers 0

    # 允许局域网访问（必须提供 api-key）
    python start_server.py --host 0.0.0.0 --api-key 你的密钥

手机连接方式:
    仅本机监听时，用 USB 转发：adb reverse tcp:8080 tcp:8080
                              然后手机访问 http://localhost:8080
    局域网监听时：http://<电脑IP>:8080（需 --host 0.0.0.0 --api-key <密钥>）

环境变量:
    {ENV_MODEL_DIRS}   额外的 GGUF 搜索目录（多个用 {os.pathsep} 分隔）
    {ENV_LLAMA_SERVER}   llama-server 可执行文件路径
    {ENV_API_KEY}      API 密钥（等价于 --api-key）
        """,
    )

    parser.add_argument(
        "--model", "-m",
        default=None,
        help="GGUF 模型文件路径（默认自动查找）"
    )
    parser.add_argument(
        "--server", "-s",
        default=None,
        help="llama-server 可执行文件路径（默认自动查找）"
    )
    parser.add_argument(
        "--models-dir",
        action="append",
        default=None,
        metavar="DIR",
        help=f"额外的 GGUF 搜索目录，可重复指定（也可用环境变量 {ENV_MODEL_DIRS}）"
    )
    parser.add_argument(
        "--llama-dir",
        action="append",
        default=None,
        metavar="DIR",
        help="额外的 llama-server 搜索目录，可重复指定"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址（默认 127.0.0.1，仅本机可访问；"
             "要让局域网访问请显式指定 0.0.0.0 并提供 --api-key）"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8080,
        help="监听端口（默认 8080）"
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help=f"API 密钥（监听非本机地址时必填；也可用环境变量 {ENV_API_KEY}）"
    )
    # 默认 0（纯 CPU），避免在小显存机器上直接 OOM。
    # 参考：RTX 3060 12GB 跑 7B Q4/Q5 模型大致可放 20~35 层，请按实际显存调整。
    parser.add_argument(
        "--gpu-layers", "-g",
        type=int,
        default=0,
        help="GPU 加速层数（默认 0 = 纯 CPU；参考：RTX 3060 12GB 可试 20~35，按显存调整）"
    )
    parser.add_argument(
        "--context", "-c",
        type=int,
        default=4096,
        help="上下文长度（默认 4096）"
    )
    parser.add_argument(
        "--threads", "-t",
        type=int,
        default=8,
        help="CPU 线程数（默认 8）"
    )
    return parser


def main():
    args = build_parser().parse_args()

    print("=" * 55)
    print("  本地 LLM 推理服务启动工具")
    print("=" * 55)

    # 安全校验：监听非本机地址时必须提供 api-key
    api_key = args.api_key or os.environ.get(ENV_API_KEY, "") or None
    if args.host not in LOOPBACK_HOSTS and not api_key:
        print(f"❌ 拒绝以无认证方式对外暴露服务：--host {args.host} 会让局域网内任何人都能访问。")
        print("   请任选一种方式：")
        print("   1) 保持默认的仅本机监听：python start_server.py")
        print("   2) 提供 API 密钥后再对外监听：")
        print("      python start_server.py --host 0.0.0.0 --api-key <密钥>")
        print("      生成一个随机密钥：")
        print('      python -c "import secrets; print(secrets.token_urlsafe(32))"')
        print(f"   3) 也可以设置环境变量 {ENV_API_KEY} 后重试。")
        sys.exit(1)

    # 查找模型
    if args.model:
        model_path = Path(args.model).expanduser()
    else:
        search_dirs = default_model_dirs()
        search_dirs.extend(Path(d).expanduser() for d in (args.models_dir or []))
        search_dirs.extend(_env_path_list(ENV_MODEL_DIRS))
        model_path = find_gguf_model(search_dirs)

    if not model_path or not model_path.is_file():
        print("❌ 未找到 GGUF 模型文件！")
        print("   请通过 --model 参数指定模型路径，或将 .gguf 文件放在以下目录之一：")
        for d in default_model_dirs():
            print(f"   - {d}")
        print(f"   也可以用 --models-dir 或环境变量 {ENV_MODEL_DIRS} 指定其他目录。")
        sys.exit(1)

    model_size_gb = model_path.stat().st_size / (1024**3)
    print(f"📦 模型: {model_path.name} ({model_size_gb:.2f} GB)")

    # 查找 llama-server
    if args.server:
        server_path = Path(args.server).expanduser()
    else:
        extra_dirs = [Path(d).expanduser() for d in (args.llama_dir or [])]
        server_path = find_llama_server(extra_dirs)

    if not server_path or not server_path.exists():
        print("❌ 未找到 llama-server！")
        print("   请通过 --server 参数指定可执行文件，或用 --llama-dir 指定它所在的目录。")
        print("   默认会查找以下位置：")
        for p in default_server_paths():
            print(f"   - {p}")
        print(f"   也可以设置环境变量 {ENV_LLAMA_SERVER} 指向 llama-server。")
        print("   如果还没有可执行文件，可以下载官方 Windows 发布包，或自行编译：")
        print("   cmake -B build -DGGML_CUDA=ON && cmake --build build --config Release -j")
        sys.exit(1)

    print(f"🔧 服务端: {server_path}")
    print(f"🌐 监听: {args.host}:{args.port}")
    if api_key:
        print(f"🔑 API 密钥: {mask_key(api_key)}（客户端需带 Authorization: Bearer <密钥>）")
    print(f"🖥️  GPU层数: {args.gpu_layers}")
    print(f"📏 上下文: {args.context}")
    print(f"🧵 线程数: {args.threads}")

    print()
    print("=" * 55)
    print("  🚀 正在启动推理服务...")
    print("=" * 55)
    print()
    if args.host in LOOPBACK_HOSTS:
        print(f"  💻 本机访问:       http://127.0.0.1:{args.port}")
        print(f"  📱 手机 USB 连接:  先运行 adb reverse tcp:{args.port} tcp:{args.port}")
        print(f"                     再访问 http://localhost:{args.port}")
        print("  ℹ️  当前仅监听本机；如需局域网直连，请改用 --host 0.0.0.0 --api-key <密钥>")
    else:
        local_ip = get_local_ip()
        print(f"  📱 手机 WiFi 连接: http://{local_ip}:{args.port}")
        print(f"  💻 本机访问:       http://127.0.0.1:{args.port}")
        print("  ⚠️  已对外开放监听，请确认 api-key 已妥善保管。")
    print()
    print("  🧪 测试命令:")
    if api_key:
        print(f'  curl http://127.0.0.1:{args.port}/v1/chat/completions \\')
        print(f'    -H "Content-Type: application/json" \\')
        print(f'    -H "Authorization: Bearer {mask_key(api_key)}" \\')
        print(f'    -d \'{{"messages":[{{"role":"user","content":"你好"}}]}}\'')
    else:
        print(f'  curl http://127.0.0.1:{args.port}/v1/chat/completions \\')
        print(f'    -H "Content-Type: application/json" \\')
        print(f'    -d \'{{"messages":[{{"role":"user","content":"你好"}}]}}\'')
    print()
    print("  按 Ctrl+C 停止服务")
    print("=" * 55)
    print()

    # 构建启动命令
    cmd = [
        str(server_path),
        "-m", str(model_path),
        "--host", args.host,
        "--port", str(args.port),
        "-ngl", str(args.gpu_layers),
        "-c", str(args.context),
        "-t", str(args.threads),
    ]
    if api_key:
        cmd.extend(["--api-key", api_key])

    try:
        proc = subprocess.Popen(cmd)
    except FileNotFoundError:
        print(f"❌ 无法启动 llama-server：找不到可执行文件 {server_path}")
        print("   请用 --server 指定正确的路径，或检查文件是否被杀毒软件隔离。")
        sys.exit(1)
    except OSError as e:
        print(f"❌ 启动 llama-server 失败: {e}")
        sys.exit(1)

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n\n正在停止服务...")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # 子进程没响应 terminate，强制结束
            proc.kill()
            proc.wait()
        print("✅ 服务已停止")


if __name__ == "__main__":
    main()
