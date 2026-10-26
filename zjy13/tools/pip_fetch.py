"""用 httpx 下载 PyPI wheel 并本地安装，绕过 pip 的 TLS 问题。

背景（本机实测）：pip 访问 PyPI 时报
``SSL: UNEXPECTED_EOF_WHILE_READING``，但同一台机器上 httpx 访问同样的 URL 完全正常。
这是 pip 使用的 TLS 栈被中间设备干扰所致。

本脚本用 httpx 解析 PyPI 的 JSON API，下载匹配当前 Python/平台的 wheel，
再交给 pip 从本地文件安装（不经过网络）。

用法：
    python tools/pip_fetch.py numpy typing_extensions filelock
    python tools/pip_fetch.py -r asr-requirements.txt
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import sysconfig
from pathlib import Path

import httpx

PYPI_JSON = "https://pypi.org/pypi/{name}/json"
MIRROR_JSON = "https://pypi.tuna.tsinghua.edu.cn/pypi/{name}/json"

WHEEL_DIR = Path(__file__).resolve().parent.parent / "_wheels"


def _tags() -> tuple[str, str, str]:
    """返回 (python_tag, abi_tag, platform_tag) 的候选集合描述。

    注意：``sysconfig.get_platform()`` 在 Windows 64 位 Python 上返回 ``win32``，
    但 wheel 的平台标签是 ``win_amd64``，两者不同，必须按架构判断而不是直接用返回值。
    """
    py = sys.version_info
    py_tag = f"cp{py.major}{py.minor}"
    abi_tag = f"cp{py.major}{py.minor}"

    if sys.platform == "win32":
        plat_tag = "win_amd64" if sys.maxsize > 2**32 else "win32"
    elif sys.platform == "darwin":
        plat_tag = "macosx"
    else:
        plat_tag = "manylinux" if sys.maxsize > 2**32 else "linux"

    return py_tag, abi_tag, plat_tag


def fetch_json(name: str) -> dict:
    for url in (MIRROR_JSON.format(name=name), PYPI_JSON.format(name=name)):
        try:
            with httpx.Client(timeout=30, trust_env=False, follow_redirects=True) as c:
                r = c.get(url)
                if r.status_code == 200:
                    return r.json()
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError(f"无法获取 {name} 的元数据")


def pick_wheel(meta: dict) -> tuple[str, str] | None:
    """选一个适配当前解释器的 wheel。返回 (url, filename)。

    优先级：
    1. 纯 Python wheel（py3-none-any / py2.py3-none-any）—— 无平台限制，最通用
    2. 匹配 cp311 + win_amd64 的专用 wheel
    3. 任意 win_amd64 的 cp311 wheel
    """
    py_tag, abi_tag, plat_tag = _tags()
    files = meta.get("urls") or []

    pure: list[tuple[str, str]] = []
    exact: list[tuple[str, str]] = []
    loose: list[tuple[str, str]] = []

    for f in files:
        fn = f["filename"]
        if not fn.endswith(".whl"):
            continue
        url = f["url"]
        if "none-any" in fn:
            pure.append((url, fn))
        elif plat_tag in fn and f"{py_tag}-{abi_tag}" in fn:
            exact.append((url, fn))
        elif plat_tag in fn and py_tag in fn:
            loose.append((url, fn))

    for bucket in (exact, pure, loose):
        if bucket:
            return bucket[0]
    return None


def pick_sdist(meta: dict) -> tuple[str, str] | None:
    for f in meta.get("urls") or []:
        fn = f["filename"]
        if fn.endswith((".tar.gz", ".zip")):
            return f["url"], fn
    return None


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  已存在，跳过: {dest.name}")
        return dest
    with httpx.Client(timeout=httpx.Timeout(30, read=180), trust_env=False,
                      follow_redirects=True) as c:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1024 * 1024):
                    f.write(chunk)
            tmp.replace(dest)
    print(f"  下载完成: {dest.name} ({dest.stat().st_size/1024:.0f} KB)")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="用 httpx 下载 wheel 并本地安装")
    ap.add_argument("packages", nargs="*", help="包名列表")
    ap.add_argument("-r", "--requirement", help="从文件读取包名（每行一个）")
    ap.add_argument("--no-install", action="store_true", help="只下载不安装")
    args = ap.parse_args()

    names = list(args.packages)
    if args.requirement:
        for line in Path(args.requirement).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                names.append(line.split("#")[0].strip())

    if not names:
        print("[错误] 没有指定包", file=sys.stderr)
        return 2

    WHEEL_DIR.mkdir(parents=True, exist_ok=True)
    local_files: list[Path] = []

    for name in names:
        print(f"[{name}]")
        try:
            meta = fetch_json(name)
        except Exception as exc:  # noqa: BLE001
            print(f"  失败: {exc}")
            continue

        picked = pick_wheel(meta)
        if not picked:
            print(f"  未找到适配 {sys.platform} 的 wheel，尝试源码包")
            picked = pick_sdist(meta)
        if not picked:
            print("  没有可下载的文件")
            continue

        url, fn = picked
        try:
            path = download(url, WHEEL_DIR / fn)
            local_files.append(path)
        except Exception as exc:  # noqa: BLE001
            print(f"  下载失败: {exc}")

    if args.no_install or not local_files:
        return 0

    print(f"\n安装 {len(local_files)} 个本地文件...")
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
           "--no-index", *[str(p) for p in local_files]]
    rc = subprocess.call(cmd)
    return rc


if __name__ == "__main__":
    sys.exit(main())
