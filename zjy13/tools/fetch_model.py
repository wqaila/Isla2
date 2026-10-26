"""从 ModelScope 直接下载模型文件到本地目录。

背景：FunASR 的 ``get_or_download_model_dir`` 调用
``snapshot_download(model, revision=..., user_agent=...)`` 时**没有传 cache_dir**，
因此 ModelScope 会使用默认路径 ``Path.home()/.cache/modelscope``（或旧版的
``~/.modelscope``）。在受限环境（沙箱、无写权限的用户目录）下会抛：

    [WinError 5] 拒绝访问。: 'C:\\Users\\<user>\\.modelscope'

绕开办法：用本脚本把模型文件下载到一个本地目录，然后把**本地路径**传给
``AutoModel(model=<本地目录>)``。FunASR 检测到路径已存在，就不再触发下载逻辑。

用法：
    python tools/fetch_model.py iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch
    python tools/fetch_model.py <model_id> --dest _models/paraformer
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

API = "https://www.modelscope.cn/api/v1/models/{mid}/repo/files"
RAW = "https://www.modelscope.cn/models/{mid}/resolve/master/{path}"

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "_models"


def list_files(mid: str, revision: str = "master") -> list[dict]:
    url = API.format(mid=mid)
    with httpx.Client(timeout=40, trust_env=False, follow_redirects=True) as c:
        r = c.get(url, params={"Revision": revision, "Recursive": "true"})
        r.raise_for_status()
        data = r.json()
    return [f for f in data.get("Data", {}).get("Files", []) if f.get("Type") == "blob"]


def download_file(mid: str, rel_path: str, dest: Path, retries: int = 3) -> int:
    url = RAW.format(mid=mid, path=rel_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=httpx.Timeout(30, read=300), trust_env=False,
                              follow_redirects=True) as c:
                with c.stream("GET", url) as r:
                    r.raise_for_status()
                    tmp = dest.with_suffix(dest.suffix + ".part")
                    n = 0
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_bytes(1024 * 1024):
                            f.write(chunk)
                            n += len(chunk)
                    tmp.replace(dest)
                    return n
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="下载 ModelScope 模型到本地目录")
    ap.add_argument("model_id")
    ap.add_argument("--dest", default=None, help="目标目录，默认为 _models/<模型名>")
    ap.add_argument("--revision", default="master")
    args = ap.parse_args()

    dest = Path(args.dest) if args.dest else DEFAULT_ROOT / args.model_id.split("/")[-1]
    dest.mkdir(parents=True, exist_ok=True)

    print(f"模型: {args.model_id}")
    print(f"目标: {dest}")

    files = list_files(args.model_id, args.revision)
    total = sum(f.get("Size", 0) for f in files)
    print(f"文件数: {len(files)}, 合计 {total/1e6:.1f} MB\n")

    t0 = time.time()
    done = 0
    for f in files:
        rel = f["Path"]
        target = dest / rel
        if target.exists() and target.stat().st_size == f.get("Size", 0):
            print(f"  跳过（已存在）: {rel}")
            done += target.stat().st_size
            continue
        print(f"  下载 {rel} ({f.get('Size',0)/1e6:.2f} MB)...", flush=True)
        n = download_file(args.model_id, rel, target)
        done += n
        el = time.time() - t0
        print(f"    完成 {n/1e6:.1f} MB | 累计 {done/1e6:.0f}/{total/1e6:.0f} MB | {el:.0f}s", flush=True)

    print(f"\n全部完成: {dest}")
    print(f"用时 {time.time()-t0:.0f}s")
    print("\n用法：把下面这个路径传给 AutoModel 的 model 参数")
    print(f'  AutoModel(model=r"{dest}")')
    return 0


if __name__ == "__main__":
    sys.exit(main())
