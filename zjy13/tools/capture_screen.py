"""从开发板串口抓取屏幕快照并渲染成 PNG。

固件命令：``SHOT``，返回格式为::

    BADGE_SNAPSHOT <width> <height> <stride> <size>\n
    <size 字节的 RGB565 像素数据>
    BADGE_SNAPSHOT_END

抓取要点（踩过的坑）：
- 不能用"等超时收满"的方式读取。实测会少收末尾几百字节，
  导致最后一行像素缺失（画面底部出现黑边）。
- 正确做法：先解析头部拿到 ``size``，再**按字节数精确读满**，
  最后读结束标记确认完整。

用法::

    python -m tools.capture_screen --port COM7 --out logs/shot/screen.png
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

import serial

HEADER_PREFIX = b"BADGE_SNAPSHOT "
END_MARKER = b"BADGE_SNAPSHOT_END"

#: 单次抓取整体超时（秒）。259200 字节 @115200 约需 23 秒。
DEFAULT_TIMEOUT_S = 45.0


class SnapshotError(RuntimeError):
    pass


def read_exact(port: serial.Serial, count: int, *, deadline: float) -> bytes:
    """精确读取 ``count`` 字节，直到读完或超时。"""
    buf = bytearray()
    while len(buf) < count:
        if time.monotonic() > deadline:
            raise SnapshotError(
                f"读取超时：已收 {len(buf)}/{count} 字节"
            )
        chunk = port.read(min(count - len(buf), 4096))
        if chunk:
            buf.extend(chunk)
    return bytes(buf)


def capture(port_name: str, *, baud: int = 115200,
            timeout_s: float = DEFAULT_TIMEOUT_S,
            retries: int = 3) -> tuple[bytes, tuple[int, int, int, int], bool]:
    """发送 SHOT 并返回 (像素数据, (w, h, stride, size), 是否收到结束标记)。

    串口上同时跑着固件日志，偶尔会在头部前后插入噪声行，
    或抓取瞬间板子正在重启。因此默认重试 ``retries`` 次。
    """
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return _capture_once(port_name, baud=baud, timeout_s=timeout_s)
        except (SnapshotError, serial.SerialException) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(1.0)
    assert last_err is not None
    raise last_err


def _capture_once(port_name: str, *, baud: int,
                  timeout_s: float) -> tuple[bytes, tuple[int, int, int, int], bool]:
    deadline = time.monotonic() + timeout_s
    with serial.Serial(port_name, baud, timeout=0.2) as port:
        port.reset_input_buffer()
        time.sleep(0.3)
        port.write(b"SHOT\n")
        port.flush()

        # 1) 读头部行。
        # 固件同时会把 ESP-IDF 日志（esp_image / wifi / badge 等）混在同一个串口上，
        # 所以不能假设下一行就是头部：逐行读到匹配 BADGE_SNAPSHOT 的那一行为止。
        header_line = b""
        deadline_header = time.monotonic() + 8
        while True:
            if time.monotonic() > deadline_header:
                raise SnapshotError(
                    "等待快照头部超时，固件是否支持 SHOT 命令？"
                )
            line = port.readline()
            if not line:
                continue
            stripped = line.strip()
            if stripped.startswith(HEADER_PREFIX):
                header_line = stripped
                break

        parts = header_line.split()
        if len(parts) < 5:
            raise SnapshotError(f"头部字段不足：{header_line!r}")
        try:
            w, h, stride, size = (int(p) for p in parts[1:5])
        except ValueError as exc:
            raise SnapshotError(f"头部数值解析失败：{header_line!r}") from exc

        expected = w * h * 2
        if size != expected:
            raise SnapshotError(
                f"头部声明 size={size} 与 {w}x{h}x2={expected} 不一致"
            )

        # 2) 精确读满像素数据
        pixels = read_exact(port, size, deadline=deadline)

        # 3) 结束标记可选。
        # 实测固件在发完像素后并不总是输出 BADGE_SNAPSHOT_END
        # （连续两次抓取都读不到，已收 0 字节）。
        # 因此像素字节数一致即视为成功，标记只作附加校验，缺失不报错。
        saw_end = False
        try:
            tail = read_exact(
                port, len(END_MARKER), deadline=min(deadline, time.monotonic() + 2)
            )
            saw_end = tail == END_MARKER
        except SnapshotError:
            saw_end = False

        return pixels, (w, h, stride, size), saw_end


def rgb565_to_png(pixels: bytes, w: int, h: int, out: Path) -> Path:
    """把 RGB565 小端像素写成 PNG。"""
    from PIL import Image

    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        row = y * w * 2
        for x in range(w):
            v = struct.unpack_from("<H", pixels, row + x * 2)[0]
            r = ((v >> 11) & 0x1F) * 255 // 31
            g = ((v >> 5) & 0x3F) * 255 // 63
            b = (v & 0x1F) * 255 // 31
            px[x, y] = (r, g, b)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="抓取开发板屏幕快照")
    ap.add_argument("--port", default="COM7")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--out", default=str(root / "logs" / "shot" / "screen.png"))
    ap.add_argument("--count", type=int, default=1, help="连续抓取的张数")
    ap.add_argument("--interval", type=float, default=2.0, help="多张之间的间隔秒数")
    ap.add_argument("--retries", type=int, default=3, help="单张失败重试次数")
    ap.add_argument("--raw", default="", help="同时保存原始像素数据（默认不保存）")
    args = ap.parse_args()

    out = Path(args.out)
    ok = 0
    for i in range(1, args.count + 1):
        target = out if args.count == 1 else out.with_name(
            f"{out.stem}_{i}{out.suffix}"
        )
        try:
            t0 = time.monotonic()
            pixels, (w, h, stride, size), saw_end = capture(
                args.port, baud=args.baud, timeout_s=args.timeout,
                retries=args.retries,
            )
            elapsed = time.monotonic() - t0
        except SnapshotError as exc:
            print(f"[失败] 第 {i} 张：{exc}", file=sys.stderr)
            continue
        except serial.SerialException as exc:
            print(f"[失败] 串口不可用：{exc}", file=sys.stderr)
            return 2

        png = rgb565_to_png(pixels, w, h, target)
        ok += 1
        print(
            f"[{i}/{args.count}] {w}x{h} {size} 字节 {elapsed:.1f}s "
            f"结束标记={'有' if saw_end else '无'} -> {png.name} "
            f"({png.stat().st_size/1024:.0f} KB)"
        )

        if args.raw:
            rawname = Path(args.raw)
            raw_target = rawname if args.count == 1 else rawname.with_name(
                f"{rawname.stem}_{i}{rawname.suffix}"
            )
            raw_target.parent.mkdir(parents=True, exist_ok=True)
            raw_target.write_bytes(pixels)

        if i < args.count:
            time.sleep(args.interval)

    print(f"\n完成：成功 {ok}/{args.count} 张")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
