#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""本轮修复的回归测试（纯标准库、离线、不联网）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_web_service.py

【为什么单独一个文件】
这四条缺陷都是 2026-10-28 起网页服务时**实测撞出来**的，全部属于
「报成功但其实失败」或「文档承诺了但实现做不到」那类 —— 正是最容易
反复回归的地方。每条都写清楚它在没有断言时会怎样被漏掉。

覆盖：
1. 输出模板保留后缀 → `xxx.mp3.mp3`（文件后缀翻倍，返回路径不存在）
2. 裸 BV 号直接喂 yt-dlp → `not a valid URL`（页面允许填、CLI 不认）
3. 搜索标题带 `<em class="keyword">` → 落盘文件名里混进 HTML
4. `-q` 只认 4 档、实现支持 7 档 → `-q 2160` 被 argparse 拒掉
   （第 4 条在 test_param_validation.py 里，这里只做交叉核对）
"""

import inspect
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ZJY5 = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ZJY5, "bilibili_downloader"))
sys.path.insert(0, ZJY5)


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


# ---- 1. 输出模板 ----

def test_output_template_strips_audio_ext():
    """音频模式的输出模板不能带着 `.mp3` 去拼 `%(ext)s`。

    漏掉时：yt-dlp 落 `xxx.mp3.m4a` → 转码成 `xxx.mp3.mp3`，
    而 `download_audio` 返回的 `xxx.mp3` **不存在**，
    上层（CLI / 网页服务）照样打印「✓ 下载完成」。
    """
    from downloader import BilibiliDownloader
    with tempfile.TemporaryDirectory() as tmp:
        d = BilibiliDownloader(output_dir=tmp, audio_format="mp3")
        args = d._build_download_args(
            video_url="https://www.bilibili.com/video/BV1xx411c7mD",
            output_path=os.path.join(tmp, "BV1xx411c7mD.mp3"),
            download_audio=True)
        tpl = args[args.index("-o") + 1]
        eq(os.path.basename(tpl), "BV1xx411c7mD.%(ext)s",
           "音频模板不该保留 .mp3")
        ok(".mp3.%(ext)s" not in tpl, f"后缀翻倍隐患仍在：{tpl}")


def test_output_template_strips_each_supported_audio_format():
    """所有可选音频格式都必须能正确拼模板（不只 mp3）。"""
    from downloader import BilibiliDownloader
    with tempfile.TemporaryDirectory() as tmp:
        for fmt in ("mp3", "m4a", "opus", "wav", "flac"):
            d = BilibiliDownloader(output_dir=tmp, audio_format=fmt)
            args = d._build_download_args(
                video_url="https://www.bilibili.com/video/BV1xx411c7mD",
                output_path=os.path.join(tmp, f"t.{fmt}"), download_audio=True)
            tpl = os.path.basename(args[args.index("-o") + 1])
            eq(tpl, "t.%(ext)s", f"{fmt} 的模板仍带原后缀")


def test_audio_expected_path_matches_audio_format():
    """`download_audio` 期望的落盘后缀必须跟 `audio_format` 一致。

    原来写死 `.mp3`：选 flac 时落的是 `xxx.flac`，函数却去查 `xxx.mp3`
    —— 文件明明下好了，却可能被判成「不存在」。
    """
    from downloader import BilibiliDownloader
    src = inspect.getsource(BilibiliDownloader.download_audio)
    ok('f"{safe_title}.mp3"' not in src,
       "download_audio 仍把后缀写死成 .mp3")
    ok("self.audio_format" in src,
       "download_audio 应根据 self.audio_format 决定后缀")


# ---- 2. 裸 BV / av 号 ----

def test_bare_bv_is_normalized_to_url():
    """页面接受裸 BV 号，但传给 yt-dlp 前必须补成完整 URL。"""
    import web_service as w
    _, url, _, _ = w._build_argv({"mode": "audio", "target": "BV1GJ411x7h7"})
    eq(url, "https://www.bilibili.com/video/BV1GJ411x7h7", "裸 BV 应补成 URL")


def test_bare_av_is_normalized_to_url():
    """裸 av 号同样要补 —— `harvest.normalize_target` 只认 BV，别指望它。"""
    import web_service as w
    _, url, _, _ = w._build_argv({"mode": "video", "target": "av80433022"})
    eq(url, "https://www.bilibili.com/video/av80433022", "裸 av 应补成 URL")


def test_full_url_is_passed_through():
    """完整链接（含 ?p= 这类查询串）必须原样保留，不能被改写。"""
    import web_service as w
    raw = "https://www.bilibili.com/video/BV1GJ411x7h7?p=2"
    _, url, _, _ = w._build_argv({"mode": "video", "target": raw})
    eq(url, raw, "完整链接应原样透传")


def test_non_bilibili_target_rejected():
    """非 B 站目标必须拒绝 —— 否则服务会变成「拿本机 IP 下载任意站点」的入口。"""
    import web_service as w
    for bad in ("https://example.com/x.mp4", "https://youtube.com/watch?v=1",
                "随便一段字", ""):
        try:
            w._build_argv({"mode": "video", "target": bad})
        except ValueError:
            continue
        raise Missing(f"应当拒绝非 B 站目标，实际放行了 {bad!r}")


# ---- 3. 搜索标题高亮 ----

def test_search_title_highlight_stripped():
    """搜索标题里的 `<em class="keyword">` 必须剥掉。"""
    from search_crawler import strip_keyword_highlight
    eq(strip_keyword_highlight('最全的<em class="keyword">Python</em>教程'),
       "最全的Python教程", "高亮标签应删除")
    eq(strip_keyword_highlight("普通标题"), "普通标题", "无标签时原样返回")
    eq(strip_keyword_highlight(""), "", "空标题返回空串")


def test_search_title_highlight_keeps_real_angle_brackets():
    """只删 `<em>` 标签，不能连带吃掉标题里真正的尖括号。"""
    from search_crawler import strip_keyword_highlight
    eq(strip_keyword_highlight("A &lt; B 的证明"), "A < B 的证明",
       "HTML 实体应还原成字符，而不是被删掉")


def test_search_request_carries_search_type():
    """搜索请求必须带 `search_type=video`，否则服务端返回 `-400 请求错误`。

    这个错误文案像签名 / 风控问题，实际只是漏了一格参数 ——
    排查时极容易走错方向，所以用断言钉住。
    """
    from search_crawler import SearchCrawler
    src = inspect.getsource(SearchCrawler.search_videos)
    ok('"search_type"' in src or "'search_type'" in src,
       "search_videos 的参数里没有 search_type（会返回 -400）")
    ok('"video"' in src, "search_type 的值应为 video")


# ---- 4. 画质档位（与 test_param_validation 交叉核对）----

def test_cli_quality_choices_cover_all_tiers():
    """`-q` 的 choices 必须覆盖 downloader 实现支持的每一档。"""
    import main as cli_mod
    impl = inspect.getsource(cli_mod.BilibiliDownloader._build_download_args)
    cli = inspect.getsource(cli_mod.main_cli)
    tiers = [h for h in ("2160", "1440", "1080", "720", "480", "360")
             if f'"{h}"' in impl]
    ok(tiers, "没能从实现里识别出画质档位")
    missing = [t for t in tiers if f'"{t}"' not in cli]
    ok(not missing, f"CLI 的 -q 缺少档位 {missing}，会被 argparse 拒掉")


# ---- 5. 网页服务的安全/健壮性约定 ----

def test_file_serving_is_confined_to_downloads():
    """产物下载接口不能把 downloads 之外的路径读出去（目录穿越）。"""
    import web_service as w
    app = w.create_app()
    client = app.test_client()
    for probe in ("/api/files/../bilibili_cookies.txt",
                  "/api/files/..%2Fbilibili_cookies.txt",
                  "/api/files/../../README.md"):
        r = client.get(probe)
        ok(r.status_code in (403, 404),
           f"{probe} 应当被拒绝，实际 {r.status_code}")


def test_subtitle_mode_disables_browser_fallback():
    """字幕模式必须带 `--no-playwright`。

    那条路会弹浏览器 + `input()` 等人工点字幕按钮；在后台服务里
    会**永久挂住**（实测撞到过），而不是失败退出。
    """
    import web_service as w
    _, _, argv, _ = w._build_argv(
        {"mode": "subtitle", "target": "BV1GJ411x7h7"})
    ok("--no-playwright" in argv, "字幕模式必须禁用浏览器回退")


def test_harvest_mode_rejected_for_non_bilibili_file():
    """harvest 模式只接受 B 站目标或存在的 urls.txt。"""
    import web_service as w
    try:
        w._build_argv({"mode": "harvest", "target": "C:\\不存在\\urls.txt"})
    except ValueError:
        return
    raise Missing("不存在的 urls.txt 应当被拒绝")


TESTS = [
    test_output_template_strips_audio_ext,
    test_output_template_strips_each_supported_audio_format,
    test_audio_expected_path_matches_audio_format,
    test_bare_bv_is_normalized_to_url,
    test_bare_av_is_normalized_to_url,
    test_full_url_is_passed_through,
    test_non_bilibili_target_rejected,
    test_search_title_highlight_stripped,
    test_search_title_highlight_keeps_real_angle_brackets,
    test_search_request_carries_search_type,
    test_cli_quality_choices_cover_all_tiers,
    test_file_serving_is_confined_to_downloads,
    test_subtitle_mode_disables_browser_fallback,
    test_harvest_mode_rejected_for_non_bilibili_file,
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
    print(f"\ntest_web_service：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
