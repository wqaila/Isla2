#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""yt-dlp 参数构建的测试（纯函数，不联网、不启动 yt-dlp）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_downloader_args.py

【为什么值得测】
`downloader.py` 有 600+ 行、此前**零测试覆盖**，而它的核心逻辑就是
「拼 yt-dlp 命令行参数」。这类代码最容易出**静默失败**的 bug：
参数名拼错时 yt-dlp 通常只警告一句然后继续跑 —— 表现为
「下载了但没字幕」「限速没生效」，很难追。

`_build_download_args` 是**纯函数**（给定输入必然得到同样的参数列表），
不需要网络、不需要 mock 进程，测试成本极低。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "bilibili_downloader"))

from downloader import BilibiliDownloader  # noqa: E402


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


def _builder(tmp_path, **kw):
    """造一个 downloader，只用来调 _build_download_args（不真下载）。"""
    d = BilibiliDownloader(output_dir=str(tmp_path), **kw)
    return d


def _args(tmp_path, **kw):
    d = _builder(tmp_path)
    build_kw = {}
    for k in ("quality", "download_audio", "download_subtitle"):
        if k in kw:
            build_kw[k] = kw.pop(k)
    return d._build_download_args(
        video_url=kw.pop("video_url", "https://www.bilibili.com/video/BV1xx411c7mD"),
        output_path=kw.pop("output_path", os.path.join(str(tmp_path), "out.mp4")),
        **build_kw,
    )


def _has_pair(args, flag, value=None):
    """参数列表里是否有 flag（可选校验紧随其后的 value）。"""
    if flag not in args:
        return False
    if value is None:
        return True
    i = args.index(flag)
    return i + 1 < len(args) and args[i + 1] == value


def test_basic_args_present(tmp_path=None):
    """基础参数必须齐全：输出模板、反爬头、重试、--newline。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        args = _args(tmp)

        ok("--no-playlist" in args, "单个视频不该下整个播放列表")
        ok(_has_pair(args, "-o"), "-o 输出模板必须存在")
        ok(any(a.endswith(".%(ext)s") for a in args), "输出模板应带 %(ext)s")

        ok(_has_pair(args, "--user-agent"), "必须有 UA（否则容易被拦）")
        ok(_has_pair(args, "--referer", "https://www.bilibili.com"), "必须有 Referer")

        # ⚠️ --newline：不加的话进度被 \r 原地刷新，
        #    管道接住时会攒成一大块、最后才冒出来（「全程没有百分比」）
        ok("--newline" in args, "--newline 必须存在（否则看不到进度）")


def test_newline_is_documented_reason():
    """回归：`--newline` 曾经缺失过，代码里有注释说明为什么必须有。"""
    import inspect
    src = inspect.getsource(BilibiliDownloader._build_download_args)
    ok("--newline" in src, "源码里应有 --newline")
    ok("\\r" in src or "进度" in src, "应有说明为什么需要它")


def test_video_mode_format_and_merge():
    """视频模式：应带 -f 选择器与 --merge-output-format。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        args = _args(tmp, quality="1080")
        ok(_has_pair(args, "-f"), "应指定 -f 格式选择器")
        sel = args[args.index("-f") + 1]
        ok("height<=1080" in sel, f"1080 档应含 height<=1080，实际 {sel}")
        ok(_has_pair(args, "--merge-output-format"), "应指定合并容器格式")
        ok("-x" not in args, "视频模式不该提取音频")


def test_quality_mapping_known_values():
    """四档画质映射应各自生效，未知值回落到 best。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        for q, expect in (("best", None), ("1080", "1080"), ("720", "720"), ("480", "480")):
            args = _args(tmp, quality=q)
            sel = args[args.index("-f") + 1]
            if expect:
                ok(f"height<={expect}" in sel, f"{q} 档应含 height<={expect}，实际 {sel}")
        # 未知值不炸，回落到 best
        args = _args(tmp, quality="不存在的档位")
        sel = args[args.index("-f") + 1]
        ok("bestvideo" in sel, f"未知档位应回落 best，实际 {sel}")


def test_audio_mode():
    """音频模式：-x + mp3 + 质量，且不该带 -f 视频选择器。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        args = _args(tmp, download_audio=True)
        ok("-x" in args, "应提取音频")
        ok(_has_pair(args, "--audio-format", "mp3"), "应转 mp3")
        ok(_has_pair(args, "--audio-quality"), "应指定音频质量")
        ok("-f" not in args, "音频模式不该带视频格式选择器")


def test_subtitle_option():
    """字幕选项：勾选时才出现。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        with_sub = _args(tmp, download_subtitle=True)
        ok("--write-auto-sub" in with_sub, "应下载自动字幕")
        ok(_has_pair(with_sub, "--sub-lang"), "应指定字幕语言")

        without = _args(tmp, download_subtitle=False)
        ok("--write-auto-sub" not in without, "没勾选时不该下载字幕")


def test_cookie_option_only_when_file_exists():
    """Cookie 文件不存在时不该传 --cookies（yt-dlp 会报错退出）。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        args = _args(tmp)
        ok("--cookies" not in args, "无 cookie 文件时不该传 --cookies")

        cookie_file = os.path.join(tmp, "ck.txt")
        with open(cookie_file, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
        d = BilibiliDownloader(output_dir=tmp, cookie_path=cookie_file)
        args2 = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"),
        )
        ok(_has_pair(args2, "--cookies", cookie_file), "有 cookie 文件时应传入")


def test_retry_and_sleep_present():
    """重试与限速参数必须存在（它们是反爬的一部分）。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        args = _args(tmp)
        for flag in ("--retries", "--fragment-retries", "--retry-sleep",
                     "--sleep-interval", "--max-sleep-interval"):
            ok(_has_pair(args, flag), f"缺少 {flag}")


def test_ssl_check_only_when_disabled():
    """默认必须**校验**证书；只有显式关闭时才加 --no-check-certificate。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        args = _args(tmp)
        ok("--no-check-certificate" not in args, "默认应校验证书")

        d = BilibiliDownloader(output_dir=tmp, verify_ssl=False)
        args2 = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"),
        )
        ok("--no-check-certificate" in args2, "显式关闭时应加上该参数")


def test_continue_resume_enabled_by_default():
    """断点续传：大文件下到一半中断后，重跑应从断点继续而不是从头下。

    现状（改动前）：没有 `--continue`，一个 2GB 视频下到 1.5GB 挂掉，
    重跑会**从头开始**。yt-dlp 原生支持 `-c`，只是没传。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        args = _args(tmp)
        ok("-c" in args or "--continue" in args,
           f"应默认开启断点续传，实际参数里没有 -c/--continue：{args}")


def test_continue_can_be_disabled():
    """允许显式关闭断点续传（极少数需要「重新下干净的」场景）。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp, resume=False)
        args = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"),
        )
        ok("-c" not in args and "--continue" not in args, "关闭时不该带续传参数")


def test_tunable_sleep_and_retries():
    """限速与重试应可配（原来 6 个参数全硬编码，用户改不了）。

    ⚠️ 这与 harvest 的 `--rate-limit` 可配是一致的 ——
    同一工具集不该有两套策略。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp, sleep_interval=0.5,
                               max_sleep_interval=2, retries=7,
                               fragment_retries=4, retry_sleep=9)
        args = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"),
        )
        eq(args[args.index("--sleep-interval") + 1], "0.5", "sleep-interval 可配")
        eq(args[args.index("--max-sleep-interval") + 1], "2", "max-sleep-interval 可配")
        eq(args[args.index("--retries") + 1], "7", "retries 可配")
        eq(args[args.index("--fragment-retries") + 1], "4", "fragment-retries 可配")
        eq(args[args.index("--retry-sleep") + 1], "9", "retry-sleep 可配")


def test_defaults_unchanged():
    """默认值必须与改动前**完全一致** —— 这是纯增量改动，不改变现有行为。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        # 视频模式
        args = _args(tmp)
        eq(args[args.index("--sleep-interval") + 1], "3", "默认 sleep-interval 仍是 3")
        eq(args[args.index("--max-sleep-interval") + 1], "10", "默认 max 仍是 10")
        eq(args[args.index("--retries") + 1], "3", "默认 retries 仍是 3")
        eq(args[args.index("--fragment-retries") + 1], "3", "默认 fragment 仍是 3")
        eq(args[args.index("--retry-sleep") + 1], "5", "默认 retry-sleep 仍是 5")
        eq(args[args.index("--merge-output-format") + 1], "mp4", "默认容器仍是 mp4")

        # ⚠️ --audio-quality 只在**音频模式**出现（视频模式不带 -x）
        a = _args(tmp, download_audio=True)
        eq(a[a.index("--audio-quality") + 1], "0", "默认音频质量仍是 0")


def test_tunable_audio_quality_and_merge_format():
    """音频质量与合并容器格式也应可配。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp, audio_quality="5",
                               merge_output_format="mkv")
        a = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"), download_audio=True)
        eq(a[a.index("--audio-quality") + 1], "5", "audio-quality 可配")

        v = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"))
        eq(v[v.index("--merge-output-format") + 1], "mkv", "merge 格式可配")


TESTS = [
    test_basic_args_present,
    test_newline_is_documented_reason,
    test_video_mode_format_and_merge,
    test_quality_mapping_known_values,
    test_audio_mode,
    test_subtitle_option,
    test_cookie_option_only_when_file_exists,
    test_retry_and_sleep_present,
    test_ssl_check_only_when_disabled,
    test_continue_resume_enabled_by_default,
    test_continue_can_be_disabled,
    test_tunable_sleep_and_retries,
    test_defaults_unchanged,
    test_tunable_audio_quality_and_merge_format,
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
    print(f"\ntest_downloader_args：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
