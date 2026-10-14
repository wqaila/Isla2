#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""下载结果**真实性**校验的测试（不联网、不启动 yt-dlp）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_download_result.py

【要解决的真实缺陷（2026-10-12 实测）】
下载一个 B 站视频，程序打印：

    ✓ 下载完成：...\\BV1GJ411x7h7.mp4
    下载完成！文件：...\\BV1GJ411x7h7.mp4

但**那个文件根本不存在** —— 实际留在目录里的是两个**分离流**：

    BV1GJ411x7h7.f100023.mp4    ← 只有画面
    BV1GJ411x7h7.f30280.m4a     ← 只有声音

根因：机器的 PATH 里**没有 ffmpeg**，yt-dlp 无法把音视频合并，
于是把两条流各自留下（文件名带 `.f<id>` 前缀）就不管了。
而代码只看 `process.returncode == 0` 就宣布成功，
**从不检查目标文件是否真的存在**。

用户看到「下载完成」后去找文件，会发现什么都没有 —— 这种
「报成功但文件不存在」是最误导的一类缺陷。
"""

import inspect
import os
import sys
import tempfile

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


def test_verify_download_rejects_missing_file():
    """目标文件不存在 → `_verify_download` 必须返回 False。

    这是核心回归：程序原来只看返回码，不看文件。
    """
    d = BilibiliDownloader(output_dir=tempfile.mkdtemp(prefix="vr_"))
    with tempfile.TemporaryDirectory() as tmp:
        ok(not d._verify_download(os.path.join(tmp, "nope.mp4")),
           "文件不存在应判失败")


def test_verify_download_rejects_empty_file():
    """0 字节文件也算失败（下载中断会留下空文件）。"""
    d = BilibiliDownloader(output_dir=tempfile.mkdtemp(prefix="vr_"))
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "empty.mp4")
        open(p, "w").close()
        ok(not d._verify_download(p), "0 字节应判失败")


def test_verify_download_accepts_real_file():
    """正常有内容的文件应通过。"""
    d = BilibiliDownloader(output_dir=tempfile.mkdtemp(prefix="vr_"))
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "ok.mp4")
        with open(p, "wb") as f:
            f.write(b"x" * 1024)
        ok(d._verify_download(p), "有内容的文件应通过")


def test_detect_leftover_fragments():
    """应能识别出「音视频分离、没合并」的残留文件。

    特征：同目录下有 `<base>.f<数字>.<ext>`。这是缺 ffmpeg 时的典型产物，
    也是用户最需要被提示的情况 —— 否则他会以为「下载失败了」，
    而实际上是**缺 ffmpeg**，装一个就能自动合并。
    """
    d = BilibiliDownloader(output_dir=tempfile.mkdtemp(prefix="fr_"))
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "BV1GJ411x7h7")
        for name in ("BV1GJ411x7h7.f100023.mp4", "BV1GJ411x7h7.f30280.m4a"):
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(b"y" * 512)
        frags = d._find_fragments(os.path.join(tmp, "BV1GJ411x7h7.mp4"))
        eq(len(frags), 2, f"应找到 2 个分离流，实际 {frags}")


def test_no_fragments_when_clean():
    """正常合并完成后不该误报分离流。"""
    d = BilibiliDownloader(output_dir=tempfile.mkdtemp(prefix="fr_"))
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "BV1.mp4"), "wb") as f:
            f.write(b"z" * 512)
        eq(d._find_fragments(os.path.join(tmp, "BV1.mp4")), [],
           "只有一个正常文件时不该报分离流")


def test_missing_result_records_failure():
    """「返回码为 0 但文件不在」必须**记入失败清单**。

    否则用户重跑时清单里没有它，永远不会被补下 ——
    而它恰恰是最需要重试（装完 ffmpeg 后）的那一类。
    """
    with tempfile.TemporaryDirectory() as tmp:
        fail_path = os.path.join(tmp, "failed.json")
        d = BilibiliDownloader(output_dir=tmp, failure_path=fail_path)

        # 直接调用校验失败的收尾逻辑（无分离流的情况）
        d._on_download_unverified(
            "https://www.bilibili.com/video/BV1GJ411x7h7",
            os.path.join(tmp, "BV1GJ411x7h7.mp4"), "标题")

        from download_failures import FailureStore
        store = FailureStore(fail_path)
        eq(len(store), 1, "应记入失败清单")
        # 分类只要是「非网络类」即可 —— 这类问题重试多少次都一样，
        # 必须先装 ffmpeg / 换画质，而不是盲目重试
        ok(store.records[0].kind not in ("network",),
           f"不该分类成网络问题（会误导用户盲目重试），实际 {store.records[0].kind}")
        ok(store.records[0].reason, "应记录失败原因")


def test_missing_result_with_fragments_records_ffmpeg_reason():
    """有分离流时，失败原因应指向 **ffmpeg**（可执行的根因），而不是泛泛的「不存在」。"""
    with tempfile.TemporaryDirectory() as tmp:
        fail_path = os.path.join(tmp, "failed.json")
        d = BilibiliDownloader(output_dir=tmp, failure_path=fail_path)
        target = os.path.join(tmp, "BV1GJ411x7h7.mp4")
        for name in ("BV1GJ411x7h7.f100023.mp4", "BV1GJ411x7h7.f30280.m4a"):
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(b"y" * 256)

        d._on_download_unverified("https://x/BV1GJ411x7h7", target, "标题")

        from download_failures import FailureStore
        rec = FailureStore(fail_path).records[0]
        ok("ffmpeg" in rec.reason.lower(),
           f"失败原因应提到 ffmpeg（用户才知道该装什么），实际：{rec.reason}")


def test_ffmpeg_hint_in_source():
    """源码里应有「缺 ffmpeg」的提示 —— 这是分离流的根因，必须告诉用户。

    只报「文件不存在」而不说原因，用户会反复重试却始终失败。
    """
    src = inspect.getsource(sys.modules["downloader"])
    ok("ffmpeg" in src.lower(), "源码里应提到 ffmpeg（分离流根因）")
    ok("合并" in src or "merge" in src.lower(), "应说明是合并失败")


def test_download_video_returns_none_when_file_missing():
    """端到端走**真实** `download_video`：yt-dlp 返回 0 但文件不在 → 必须返回 None。

    ⚠️ 补这条是因为变异测试暴露了盲区：上面那条 `main_cli` 测试用 mock
    替掉了 downloader，**绕过了 `download_video` 内部真正的校验逻辑** ——
    于是「把校验分支删掉」这个变异**抓不到**。

    做法：把 `_build_download_args` 换成必然成功的假命令
    （`python -c pass`，返回码 0），但让它**不产生任何文件** ——
    这精确复现了「返码 0 但文件不存在」的真实场景。
    """
    import tempfile
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp)
        # 假命令：返回码 0，但不产出任何文件
        d._build_download_args = lambda *a, **kw: [
            sys.executable, "-c", "pass"]
        # check_ytdlp 直接放行（否则会尝试装 yt-dlp）
        d.ytdlp_path = "python -m yt_dlp"
        d._use_python_module = True

        result = d.download_video(
            "https://www.bilibili.com/video/BV1xx411c7mD", title="t")
        eq(result, None,
           "yt-dlp 返回 0 但目标文件不存在时，必须返回 None（不能谎报成功）")


def test_cli_exit_code_on_download_failure():
    """CLI 在下载失败时**必须返回非 0 退出码**。

    ⚠️ 既有缺陷（2026-10-12 实测）：`main_cli` 的下载分支原来只
    `print("下载失败")` 然后隐式 `return None` → **退出码恒为 0**。
    脚本在 CI / shell 里无法用 `$?` 判断成败，只能去 grep 输出文本。

    ⚠️ 这里**不用源码字符串匹配**做断言 —— 第一版这么写过，
    但 `download_video` 在文件里有 3 处调用（交互模式 2 处 + CLI 1 处），
    `find`/`rfind` 都容易命中错的那处，测试变得脆弱且会误报。
    改为**直接调用 `main_cli`** 并注入假 downloader 来观察真实返回码。
    """
    import argparse
    import tempfile
    from unittest import mock

    import main as cli_mod

    with tempfile.TemporaryDirectory() as tmp:
        for bad_result in (None,):
            fake = mock.MagicMock()
            fake.download_video.return_value = bad_result
            with mock.patch.object(cli_mod, "BilibiliDownloader", return_value=fake), \
                 mock.patch.object(cli_mod, "SubtitleExtractor", return_value=mock.MagicMock()), \
                 mock.patch.object(cli_mod.CookieManager, "is_logged_in", return_value=False):
                rc = cli_mod.main_cli([
                    "https://www.bilibili.com/video/BV1xx411c7mD",
                    "--output", tmp,
                ])
            eq(rc, 1, "下载返回 None 时 main_cli 应返回 1")


def test_cli_exit_code_on_download_success():
    """成功时应返回 0（不能因为加了失败分支就把成功也判成失败）。"""
    import tempfile
    from unittest import mock

    import main as cli_mod

    with tempfile.TemporaryDirectory() as tmp:
        fake = mock.MagicMock()
        fake.download_video.return_value = os.path.join(tmp, "ok.mp4")
        with mock.patch.object(cli_mod, "BilibiliDownloader", return_value=fake), \
             mock.patch.object(cli_mod, "SubtitleExtractor", return_value=mock.MagicMock()), \
             mock.patch.object(cli_mod.CookieManager, "is_logged_in", return_value=False):
            rc = cli_mod.main_cli([
                "https://www.bilibili.com/video/BV1xx411c7mD",
                "--output", tmp,
            ])
        ok(rc in (0, None), f"成功时应返回 0/None，实际 {rc!r}")


TESTS = [
    test_verify_download_rejects_missing_file,
    test_verify_download_rejects_empty_file,
    test_verify_download_accepts_real_file,
    test_detect_leftover_fragments,
    test_no_fragments_when_clean,
    test_missing_result_records_failure,
    test_missing_result_with_fragments_records_ffmpeg_reason,
    test_download_video_returns_none_when_file_missing,
    test_ffmpeg_hint_in_source,
    test_cli_exit_code_on_download_failure,
    test_cli_exit_code_on_download_success,
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
    print(f"\ntest_download_result：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
