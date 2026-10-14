#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ffmpeg 自动发现的测试（不联网、不下载）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_ffmpeg_locate.py

【为什么需要自动发现】
B 站高清视频**音视频分离**，合并需要 ffmpeg。但用户的 ffmpeg 常常
**不在 PATH 里**，却在机器上某个角落躺着，例如：

- `imageio-ffmpeg` 包自带的：`<venv>/Lib/site-packages/imageio_ffmpeg/binaries/`
- 各种工具的私有副本

找不到时用户会以为「没装 ffmpeg」而去重新下载 100MB —— 而其实机器上就有。
所以先探测常见位置，再退回 PATH 查找。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "bilibili_downloader"))

from downloader import (  # noqa: E402
    BilibiliDownloader,
    find_ffmpeg,
)


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


def test_find_ffmpeg_returns_none_or_path():
    """`find_ffmpeg()` 要么返回一个存在的路径，要么返回 None（不能返回假路径）。"""
    got = find_ffmpeg()
    if got is not None:
        ok(os.path.exists(got), f"返回的路径必须真实存在：{got}")
        ok(os.path.basename(got).lower().startswith("ffmpeg"),
           f"文件名应是 ffmpeg*，实际 {got}")


def test_find_ffmpeg_in_temp_dir():
    """给定一个含 `ffmpeg.exe` 的目录应能找到它（用于验证探测逻辑本身）。"""
    with tempfile.TemporaryDirectory() as tmp:
        exe = os.path.join(tmp, "ffmpeg.exe")
        with open(exe, "wb") as f:
            f.write(b"MZ")          # 内容无所谓，只看存在性与命名
        got = find_ffmpeg(search_dirs=[tmp])
        eq(got, exe, "应能在指定目录里找到 ffmpeg.exe")


def test_find_ffmpeg_finds_imageio_bundled():
    """应能发现 `imageio-ffmpeg` 包自带的副本。

    ⚠️ 这条是**变异测试逼出来的**：原来只有
    `test_find_ffmpeg_returns_none_or_path`（「要么 None 要么有效路径」），
    而 **None 也满足那个断言** —— 于是「删掉 imageio 探测」这个变异
    **抓不到**。现在明确断言：本机装了 imageio_ffmpeg 时，
    它自带的那份必须能被找到。

    （本机 venv 里确实有：`imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe`）
    """
    try:
        import imageio_ffmpeg
    except ImportError:
        return                      # 没装这个包就跳过（不是失败）
    expected = imageio_ffmpeg.get_ffmpeg_exe()
    if not expected or not os.path.exists(expected):
        return
    got = find_ffmpeg()
    ok(got is not None, "装了 imageio_ffmpeg 就该能找到它自带的 ffmpeg")
    eq(os.path.normcase(os.path.abspath(got)),
       os.path.normcase(os.path.abspath(expected)),
       "找到的应是 imageio_ffmpeg 自带那份")


def test_find_ffmpeg_skips_unnamed():
    """目录里只有别的 exe 时不该误报。

    ⚠️ 必须**隔离其他来源**：`find_ffmpeg()` 除了 search_dirs 还会查 PATH
    与 imageio_ffmpeg 副本。若只断言「返回 None」，在装了 imageio_ffmpeg
    的环境（如本项目 venv）里会因为**找到了别处的 ffmpeg** 而失败 ——
    那不是被测逻辑的问题。所以这里直接调底层匹配函数。
    """
    from downloader import _looks_like_ffmpeg

    ok(not _looks_like_ffmpeg("not-ffmpeg.exe"), "不该把其他 exe 当成 ffmpeg")
    ok(not _looks_like_ffmpeg("ffprobe.exe"), "ffprobe 不是 ffmpeg")
    ok(not _looks_like_ffmpeg("README.md"), "非 exe 不该匹配")
    ok(_looks_like_ffmpeg("ffmpeg.exe"), "标准名应匹配")
    ok(_looks_like_ffmpeg("ffmpeg"), "无扩展名的也应匹配（Linux）")


def test_find_ffmpeg_accepts_prefixed_name():
    """应能识别 `ffmpeg-win-x86_64-v7.1.exe` 这类**带后缀的**文件名。

    ⚠️ 真实场景：`imageio-ffmpeg` 包自带的就叫这个名字，
    严格的 `== "ffmpeg.exe"` 匹配会漏掉它。
    """
    with tempfile.TemporaryDirectory() as tmp:
        exe = os.path.join(tmp, "ffmpeg-win-x86_64-v7.1.exe")
        with open(exe, "wb") as f:
            f.write(b"MZ")
        got = find_ffmpeg(search_dirs=[tmp])
        eq(got, exe, "应识别带版本后缀的 ffmpeg")


def test_downloader_exposes_ffmpeg_location():
    """downloader 应把找到的 ffmpeg 暴露出来，供拼参数时使用。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp)
        ok(hasattr(d, "ffmpeg_path"), "应有 ffmpeg_path 属性")
        if d.ffmpeg_path:
            ok(os.path.exists(d.ffmpeg_path), "ffmpeg_path 必须真实存在")


def test_ffmpeg_location_passed_to_ytdlp():
    """找到 ffmpeg 时，应把它通过 `--ffmpeg-location` 传给 yt-dlp。

    ⚠️ 这一条是关键：只「找到」但**不传给 yt-dlp**，等于没找到 ——
    yt-dlp 仍然会去 PATH 里找、仍然合并失败。
    """
    with tempfile.TemporaryDirectory() as tmp:
        # 造一个假的 ffmpeg
        fake = os.path.join(tmp, "ffmpeg.exe")
        with open(fake, "wb") as f:
            f.write(b"MZ")
        d = BilibiliDownloader(output_dir=tmp, ffmpeg_path=fake)
        args = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"))
        ok("--ffmpeg-location" in args,
           f"应传 --ffmpeg-location，实际参数：{args}")
        idx = args.index("--ffmpeg-location")
        eq(args[idx + 1], fake, "应指向探测到的 ffmpeg")


def test_no_ffmpeg_location_when_not_found():
    """没找到 ffmpeg 时不该硬塞一个不存在的路径（会让 yt-dlp 直接报错）。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp, ffmpeg_path=None)
        # 显式把探测结果置空，模拟「机器上真的没有」
        d.ffmpeg_path = None
        args = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"))
        ok("--ffmpeg-location" not in args,
           "没找到时不该传 --ffmpeg-location")


TESTS = [
    test_find_ffmpeg_returns_none_or_path,
    test_find_ffmpeg_in_temp_dir,
    test_find_ffmpeg_finds_imageio_bundled,
    test_find_ffmpeg_skips_unnamed,
    test_find_ffmpeg_accepts_prefixed_name,
    test_downloader_exposes_ffmpeg_location,
    test_ffmpeg_location_passed_to_ytdlp,
    test_no_ffmpeg_location_when_not_found,
]


def run():
    passed = 0
    failed = 0
    for test in TESTS:
        try:
            test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except Exception as e:                      # 打印出来，别让失败悄悄过去
            print(f"  ✗ {test.__name__}：{type(e).__name__}: {e}")
            failed += 1
    print(f"\ntest_ffmpeg_locate：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
