#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""下载器增强功能的测试（纯函数 / 临时文件，不联网）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_downloader_extras.py

覆盖四件事：
1. **yt-dlp 安装不再无限挂起**（原来那个 `pip install` 没有 timeout）
2. **消费已有的 `.info.json`**（数据一直在本地，没人读）
3. **弹幕（danmaku）支持**（完全新增）
4. **画质档位细化**（原来只有 4 档，且无音频码率选择）
"""

import inspect
import json
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


# ---------------------------------------------------------------- 1. 安装超时

def test_install_has_timeout():
    """`pip install` 必须有 timeout —— 否则网络卡住时会**无限挂起**。

    ⚠️ 这是实测踩到的：`ytdlp_path` 为假时会走联网安装分支，
    沙箱里挂死了 3 个后台 job（连 kill 都要等）。
    """
    src = inspect.getsource(BilibiliDownloader.check_ytdlp)
    ok("timeout" in src, "check_ytdlp 里的 subprocess.run 必须带 timeout")


def test_install_timeout_is_reasonable():
    """超时值要合理：太短会误判（正常安装要几十秒），太长等于没设。

    值既可以写成字面量，也可以引用常量 —— 两种情况都接受，
    但必须能解析出一个落在合理区间的秒数。
    """
    import re
    import downloader as mod

    src = inspect.getsource(BilibiliDownloader.check_ytdlp)
    m = re.search(r"timeout\s*=\s*([A-Za-z_][A-Za-z0-9_]*|\d+)", src)
    ok(m, "应能解析出 timeout 参数")
    token = m.group(1)
    if token.isdigit():
        val = int(token)
    else:
        val = getattr(mod, token, None)
        ok(val is not None, f"timeout 引用的常量 {token} 应存在于模块里")
    ok(30 <= val <= 600, f"timeout 应在 30~600 秒之间，实际 {val}")


# ---------------------------------------------------------------- 2. info.json

def test_parse_info_json_basic():
    """能从 yt-dlp 的 info.json 里取出关键元数据。"""
    from downloader import parse_info_json

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "x.info.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "id": "BV1xx411c7mD",
                "title": "测试视频",
                "upload_date": "20260928",
                "duration": 123.4,
                "description": "简介里有角色名",
                "uploader": "某UP",
                "chapters": [{"title": "第一段"}, {"title": "第二段"}],
            }, f, ensure_ascii=False)

        meta = parse_info_json(path)
        eq(meta["video_id"], "BV1xx411c7mD", "BV 号")
        eq(meta["title"], "测试视频", "标题")
        eq(meta["upload_date"], "20260928", "发布时间（原始格式保留）")
        eq(meta["date"], "2026-09-28", "同时给出可读日期")
        eq(meta["duration"], 123.4, "时长")
        eq(len(meta["chapters"]), 2, "分P/章节")


def test_parse_info_json_missing_or_corrupt():
    """文件不存在 / 坏 JSON 都返回 None，不能抛异常。"""
    from downloader import parse_info_json
    with tempfile.TemporaryDirectory() as tmp:
        eq(parse_info_json(os.path.join(tmp, "nope.json")), None, "不存在返回 None")
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{ 不是 json")
        eq(parse_info_json(bad), None, "坏 JSON 返回 None")
        eq(parse_info_json(None), None, "None 安全")


def test_info_json_enabled_by_default():
    """应默认写出 info.json（否则 #4 无从消费）。"""
    import tempfile as _t
    with _t.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp)
        args = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"))
        ok("--write-info-json" in args, "应默认写 info.json")


def test_info_json_can_be_disabled():
    """允许关掉（不想多出一堆 .info.json 的人）。"""
    import tempfile as _t
    with _t.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp, write_info_json=False)
        args = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"))
        ok("--write-info-json" not in args, "关闭时不该写")


# ---------------------------------------------------------------- 3. 弹幕

def test_danmaku_args_when_enabled():
    """开启弹幕时应加对应 yt-dlp 参数。"""
    import tempfile as _t
    with _t.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp, download_danmaku=True)
        args = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"))
        ok("--write-subs" in args or "--write-auto-subs" in args
           or "--sub-langs" in args or "--write-comments" in args,
           f"应加入弹幕相关参数，实际：{args}")


def test_danmaku_off_by_default():
    """默认不下载弹幕（它体积可能比视频还大，且多数场景不需要）。

    ⚠️ 断言必须匹配**实际使用的参数名**。第一版只查 `--write-comments`，
    而实现用的是 `--write-subs` —— 结果「把弹幕改成默认开启」这个变异
    测试**抓不到**。现在直接比对「开启态」与「关闭态」的差异。
    """
    import tempfile as _t
    with _t.TemporaryDirectory() as tmp:
        default_args = BilibiliDownloader(output_dir=tmp)._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"))
        on_args = BilibiliDownloader(output_dir=tmp, download_danmaku=True)._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"))

        # 关键不变式：默认态的参数列表必须与开启态**不同**
        ok(default_args != on_args,
           "默认态与开启弹幕态的参数列表不应相同 —— 否则说明默认就开了")
        # 且默认态不该出现弹幕专属参数
        ok("danmaku" not in " ".join(default_args).lower(),
           f"默认不该出现 danmaku 相关参数，实际：{default_args}")


def test_danmaku_parse_xml():
    """能把 B 站弹幕 XML 解析成结构化列表。

    B 站弹幕格式：`<d p="时间,模式,字号,颜色,时间戳,池,用户,行ID">文本</d>`
    """
    from downloader import parse_danmaku_xml

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<i><chatserver>chat.bilibili.com</chatserver>'
        '<d p="1.5,1,25,16777215,1700000000,0,abc,123">第一条弹幕</d>'
        '<d p="2.5,1,25,16711680,1700000001,0,def,124">第二条弹幕</d>'
        '</i>'
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "d.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)

        items = parse_danmaku_xml(path)
        eq(len(items), 2, "解析出 2 条")
        eq(items[0]["text"], "第一条弹幕", "文本")
        eq(items[0]["time"], 1.5, "时间点")
        eq(items[1]["color"], 16711680, "颜色")


def test_danmaku_parse_missing_or_corrupt():
    """弹幕文件缺失/损坏返回空列表，不抛异常。"""
    from downloader import parse_danmaku_xml
    with tempfile.TemporaryDirectory() as tmp:
        eq(parse_danmaku_xml(os.path.join(tmp, "no.xml")), [], "不存在返回空")
        bad = os.path.join(tmp, "bad.xml")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("<i><d p='坏的'>x</d></i>")
        eq(parse_danmaku_xml(bad), [], "坏 p 属性跳过而非崩溃")


# ---------------------------------------------------------------- 4. 画质档位

def test_more_quality_tiers():
    """画质档位应更细：至少支持 2160(4K) / 1440(2K) / 1080 / 720 / 480 / 360。"""
    import tempfile as _t
    with _t.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp)
        for q, height in (("2160", "2160"), ("1440", "1440"), ("1080", "1080"),
                          ("720", "720"), ("480", "480"), ("360", "360")):
            args = d._build_download_args(
                video_url="https://www.bilibili.com/video/BV1xx411c7mD",
                output_path=os.path.join(tmp, "o.mp4"), quality=q)
            sel = args[args.index("-f") + 1]
            ok(f"height<={height}" in sel, f"{q} 档应含 height<={height}，实际 {sel}")


def test_audio_only_format_choice():
    """音频格式应可选（原来写死 mp3）。"""
    import tempfile as _t
    with _t.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp, audio_format="m4a")
        args = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.m4a"), download_audio=True)
        eq(args[args.index("--audio-format") + 1], "m4a", "音频格式可配")


def test_video_only_no_audio():
    """支持「只要画面不要声音」（做 OCR/视觉任务时有用）。

    ⚠️ 断言要比对**开启与关闭两种状态**。第一版只断言 `video_only=True` 时
    不含 `+bestaudio`，但把实现里的 `if self.video_only` 改成 `if False`
    后，`video_only` 属性仍为真、后续逻辑碰巧也走进了无音频分支 ——
    变异**抓不到**。现在改成对比两个状态的差异。
    """
    import tempfile as _t
    with _t.TemporaryDirectory() as tmp:
        vo = BilibiliDownloader(output_dir=tmp, video_only=True)
        normal = BilibiliDownloader(output_dir=tmp, video_only=False)

        vo_args = vo._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"))
        normal_args = normal._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"))

        vo_sel = vo_args[vo_args.index("-f") + 1]
        normal_sel = normal_args[normal_args.index("-f") + 1]

        ok("+bestaudio" not in vo_sel, f"只要画面时不该拼音频，实际 {vo_sel}")
        ok("+bestaudio" in normal_sel, f"默认应带音频，实际 {normal_sel}")
        # 关键：两者必须真的不同（防止「属性没被用上」的静默失效）
        ok(vo_sel != normal_sel,
           "video_only 必须真的改变 format 选择器，否则该开关是失效的")


def test_default_quality_unchanged():
    """默认行为不变：仍是 best 且带音频。"""
    import tempfile as _t
    with _t.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp)
        args = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "o.mp4"))
        sel = args[args.index("-f") + 1]
        ok("bestvideo" in sel and "+bestaudio" in sel, f"默认仍应 best+audio，实际 {sel}")
        eq(args[args.index("--audio-format") + 1] if "--audio-format" in args else "mp3",
           "mp3", "默认音频格式仍是 mp3")


TESTS = [
    test_install_has_timeout,
    test_install_timeout_is_reasonable,
    test_parse_info_json_basic,
    test_parse_info_json_missing_or_corrupt,
    test_info_json_enabled_by_default,
    test_info_json_can_be_disabled,
    test_danmaku_args_when_enabled,
    test_danmaku_off_by_default,
    test_danmaku_parse_xml,
    test_danmaku_parse_missing_or_corrupt,
    test_more_quality_tiers,
    test_audio_only_format_choice,
    test_video_only_no_audio,
    test_default_quality_unchanged,
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
    print(f"\ntest_downloader_extras：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
