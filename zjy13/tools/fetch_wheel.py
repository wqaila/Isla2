"""分块并发下载器：用于大 wheel 文件。

单连接下载被限速时，用 Range 请求分块并发可以显著提速。
支持断点续传（已存在的分块跳过）。

用法：
    python tools/fetch_wheel.py <url> <目标文件> [--chunks 16]
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

CHUNK_MB = 8
PARTS_DIR_SUFFIX = ".parts"


def probe(url: str) -> tuple[int, bool]:
    with httpx.Client(timeout=30, trust_env=False, follow_redirects=True) as c:
        r = c.head(url)
        if r.status_code >= 400 or "content-length" not in r.headers:
            r = c.get(url, headers={"Range": "bytes=0-0"})
        size = int(r.headers.get("content-length") or 0)
        accepts = r.headers.get("accept-ranges", "").lower() == "bytes" or r.status_code == 206
        return size, accepts


def download_part(url: str, start: int, end: int, path: Path, retries: int = 5) -> int:
    expect = end - start + 1
    if path.exists() and path.stat().st_size == expect:
        return expect
    for attempt in range(retries):
        try:
            written = 0
            with httpx.Client(timeout=httpx.Timeout(30, read=120), trust_env=False,
                              follow_redirects=True) as c:
                with c.stream("GET", url, headers={"Range": f"bytes={start}-{end}"}) as r:
                    if r.status_code not in (200, 206):
                        raise RuntimeError(f"HTTP {r.status_code}")
                    with open(path, "wb") as f:
                        for chunk in r.iter_bytes(512 * 1024):
                            f.write(chunk)
                            written += len(chunk)
            if written == expect:
                return written
            raise RuntimeError(f"大小不符 {written} != {expect}")
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("dest")
    ap.add_argument("--chunks", type=int, default=16)
    ap.add_argument("--chunk-mb", type=int, default=CHUNK_MB)
    args = ap.parse_args()

    dest = Path(args.dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    size, accepts = probe(args.url)
    if size <= 0:
        print("[错误] 无法获取文件大小")
        return 1
    print(f"文件大小: {size / 1e6:.0f} MB, 支持 Range: {accepts}")

    if not accepts:
        print("[回退] 服务器不支持 Range，使用单连接")
        with httpx.Client(timeout=httpx.Timeout(60, read=300), trust_env=False,
                          follow_redirects=True) as c:
            with c.stream("GET", args.url) as r:
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes(4 * 1024 * 1024):
                        f.write(chunk)
        print(f"完成 {dest}")
        return 0

    step = args.chunk_mb * 1024 * 1024
    ranges = [(s, min(s + step - 1, size - 1)) for s in range(0, size, step)]
    parts_dir = Path(str(dest) + PARTS_DIR_SUFFIX)
    parts_dir.mkdir(exist_ok=True)

    print(f"分 {len(ranges)} 块，并发 {args.chunks}")

    t0 = time.time()
    done = 0
    lock = threading.Lock()

    def task(idx: int, rng: tuple[int, int]) -> int:
        nonlocal done
        path = parts_dir / f"{idx:05d}.part"
        n = download_part(args.url, rng[0], rng[1], path)
        with lock:
            done += n
            if done % (200 * 1024 * 1024) < step:
                el = time.time() - t0
                print(f"  {done/1e6:6.0f}/{size/1e6:.0f} MB  {el:4.0f}s  {done/1e6/el:.2f} MB/s",
                      flush=True)
        return n

    with ThreadPoolExecutor(max_workers=args.chunks) as ex:
        futures = [ex.submit(task, i, r) for i, r in enumerate(ranges)]
        for f in as_completed(futures):
            f.result()

    print(f"合并 {len(ranges)} 个分块...")
    with open(dest, "wb") as out:
        for i in range(len(ranges)):
            p = parts_dir / f"{i:05d}.part"
            with open(p, "rb") as f:
                while True:
                    buf = f.read(8 * 1024 * 1024)
                    if not buf:
                        break
                    out.write(buf)

    actual = dest.stat().st_size
    el = time.time() - t0
    print(f"完成 {actual/1e6:.0f} MB / {size/1e6:.0f} MB, 用时 {el:.0f}s, 均速 {actual/1e6/el:.2f} MB/s")

    if actual != size:
        print("[警告] 大小与预期不符，请校验")
        return 1

    for p in parts_dir.glob("*.part"):
        p.unlink()
    parts_dir.rmdir()
    return 0


if __name__ == "__main__":
    sys.exit(main())
