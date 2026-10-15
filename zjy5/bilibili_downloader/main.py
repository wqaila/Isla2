#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bilibili 视频/音频下载器 - 主入口
支持命令行和交互式使用
"""

import os
import sys
import argparse
import time
from typing import Optional

# 包内模块导入：作为包导入时用相对导入，直接以脚本方式运行时回退
try:
    from .downloader import BilibiliDownloader
    from .cookie_manager import CookieManager
    from .search_crawler import SearchCrawler, SearchDownloader, SEARCH_ORDERS
    from .channel_crawler import ChannelDownloader, CHANNEL_ORDERS
    from .subtitle_extractor import SubtitleExtractor
    from .harvest import SubtitleHarvester
except ImportError:
    from downloader import BilibiliDownloader
    from cookie_manager import CookieManager
    from search_crawler import SearchCrawler, SearchDownloader, SEARCH_ORDERS
    from channel_crawler import ChannelDownloader, CHANNEL_ORDERS
    from subtitle_extractor import SubtitleExtractor
    from harvest import SubtitleHarvester

try:
    from . import __version__
except ImportError:
    __version__ = "1.0.0"


def print_banner():
    """打印欢迎横幅"""
    banner = """
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║        Bilibili 视频/音频下载器                            ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
    """
    print(banner)


def print_menu():
    """打印主菜单"""
    print("\n" + "=" * 50)
    print("请选择操作模式:")
    print("=" * 50)
    print("1. 下载单个视频（含字幕）")
    print("2. 下载播放列表/合集")
    print("3. 搜索并下载视频")
    print("4. 下载频道/空间视频")
    print("5. 仅下载字幕")
    print("6. 配置设置")
    print("7. 批量字幕收割（UP 空间 / urls.txt 清单）")
    print("8. 退出")
    print("=" * 50)


def get_user_choice() -> str:
    """获取用户选择"""
    return input("\n请输入选项 (1-8): ").strip()


def download_single_video():
    """下载单个视频（含字幕）"""
    url = input("\n请输入视频 URL: ").strip()
    if not url:
        print("错误：URL 不能为空")
        return
    
    cookie_manager = CookieManager()
    cookie_path = cookie_manager.cookie_path if cookie_manager.is_logged_in() else None
    downloader = BilibiliDownloader(cookie_path=cookie_path)
    
    # 获取自定义文件名（可选）
    custom_name = input("请输入自定义文件名 (直接回车使用视频原标题): ").strip()
    if not custom_name:
        custom_name = None
    
    # 获取输出目录
    output_dir = input("请输入输出目录 (直接回车使用默认 'downloads'): ").strip()
    if not output_dir:
        output_dir = "downloads"
    
    # 是否下载字幕
    download_sub = input("是否下载 AI 字幕？(y/n, 默认 n): ").strip().lower()
    download_subtitle = download_sub == 'y'
    
    # 获取质量选项
    print("\n质量选项:")
    print("1. 最高画质")
    print("2. 1080P")
    print("3. 720P")
    print("4. 480P")
    quality = input("请选择质量 (直接回车使用最高画质): ").strip()
    
    quality_map = {
        "1": "best",
        "2": "1080",
        "3": "720",
        "4": "480"
    }
    quality_value = quality_map.get(quality, "best")
    
    # 下载视频
    print("\n开始下载...")
    try:
        result = downloader.download_video(
            url, 
            title=custom_name, 
            output_dir=output_dir, 
            quality=quality_value,
            download_subtitle=download_subtitle
        )
        if result:
            print(f"\n下载完成！文件保存在：{result}")
        else:
            print("\n下载失败")
    except Exception as e:
        print(f"\n下载出错：{e}")


def download_playlist():
    """下载播放列表"""
    url = input("\n请输入播放列表/合集 URL: ").strip()
    if not url:
        print("错误：URL 不能为空")
        return
    
    cookie_manager = CookieManager()
    cookie_path = cookie_manager.cookie_path if cookie_manager.is_logged_in() else None
    downloader = BilibiliDownloader(cookie_path=cookie_path)
    
    # 获取输出目录
    output_dir = input("请输入输出目录 (直接回车使用默认 'downloads'): ").strip()
    if not output_dir:
        output_dir = "downloads"
    
    # 下载播放列表
    print("\n开始下载播放列表...")
    try:
        results = downloader.download_playlist(url, output_dir=output_dir)
        print(f"\n下载完成！共下载 {len(results)} 个视频")
        for result in results:
            print(f"  - {result}")
    except Exception as e:
        print(f"\n下载出错：{e}")


def search_and_download():
    """搜索并下载视频"""
    keyword = input("\n请输入搜索关键词：").strip()
    if not keyword:
        print("错误：关键词不能为空")
        return
    
    crawler = SearchCrawler()
    
    # 获取搜索结果数量
    num_str = input("获取多少个搜索结果 (默认 10): ").strip()
    num = int(num_str) if num_str.isdigit() else 10
    
    # 获取页面数量
    page_str = input("获取多少页结果 (默认 1): ").strip()
    pages = int(page_str) if page_str.isdigit() else 1
    
    print("\n正在搜索...")
    try:
        videos = crawler.search(keyword, num=num, pages=pages)
        if not videos:
            print("没有找到相关视频")
            return
        
        print(f"\n找到 {len(videos)} 个视频:")
        for i, video in enumerate(videos[:20], 1):  # 最多显示 20 个
            print(f"{i}. {video['title']} (播放：{video.get('play_count', 'N/A')})")
        
        # 选择下载
        choice = input("\n请输入要下载的视频编号 (用逗号分隔，如 1,3,5): ").strip()
        if not choice:
            print("已取消")
            return
        
        indices = [int(x.strip()) - 1 for x in choice.split(",")]
        
        cookie_manager = CookieManager()
        cookie_path = cookie_manager.cookie_path if cookie_manager.is_logged_in() else None
        downloader = BilibiliDownloader(cookie_path=cookie_path)
        output_dir = input("请输入输出目录 (直接回车使用默认 'downloads'): ").strip()
        if not output_dir:
            output_dir = "downloads"
        
        print("\n开始下载...")
        for idx in indices:
            if 0 <= idx < len(videos):
                video = videos[idx]
                print(f"\n下载：{video['title']}")
                try:
                    result = downloader.download_video(video['url'], output_dir=output_dir)
                    if result:
                        print(f"  完成：{result}")
                except Exception as e:
                    print(f"  失败：{e}")
                
                # 避免请求过快
                time.sleep(2)
    except Exception as e:
        print(f"\n搜索出错：{e}")


def download_channel():
    """下载频道/空间视频"""
    print("\n请选择类型:")
    print("1. UP 主空间")
    print("2. 频道页面")
    type_choice = input("请输入选项 (1-2): ").strip()
    
    url = input("\n请输入 URL: ").strip()
    if not url:
        print("错误：URL 不能为空")
        return
    
    cookie_manager = CookieManager()
    cookie_path = cookie_manager.cookie_path if cookie_manager.is_logged_in() else None
    downloader = BilibiliDownloader(cookie_path=cookie_path)
    
    output_dir = input("请输入输出目录 (直接回车使用默认 'downloads'): ").strip()
    if not output_dir:
        output_dir = "downloads"
    
    print("\n开始下载...")
    try:
        if type_choice == "1":
            results = downloader.download_up_videos(url, output_dir=output_dir)
        else:
            results = downloader.download_channel(url, output_dir=output_dir)
        
        print(f"\n下载完成！共下载 {len(results)} 个视频")
    except Exception as e:
        print(f"\n下载出错：{e}")


def extract_subtitle():
    """仅下载字幕"""
    url = input("\n请输入视频 URL: ").strip()
    if not url:
        print("错误：URL 不能为空")
        return
    
    cookie_manager = CookieManager()
    cookie_path = cookie_manager.cookie_path if cookie_manager.is_logged_in() else None
    extractor = SubtitleExtractor(cookie_path=cookie_path)
    
    output_dir = input("请输入输出目录 (直接回车使用默认 'downloads'): ").strip()
    if not output_dir:
        output_dir = "downloads"
    
    custom_name = input("请输入自定义文件名 (直接回车使用视频 ID): ").strip()
    if not custom_name:
        custom_name = None
    
    print("\n正在下载 AI 字幕...")
    try:
        if custom_name:
            output_path = os.path.join(output_dir, f"{custom_name}.txt")
            result = extractor.extract_subtitle(url, output_dir=output_dir)
            # 重命名文件
            if result and result != output_path:
                import shutil
                shutil.move(result, output_path)
                result = output_path
        else:
            result = extractor.extract_subtitle(url, output_dir=output_dir)
        
        if result:
            print(f"\n下载完成！字幕文件：{result}")
        else:
            print("\n该视频没有 AI 字幕")
    except Exception as e:
        print(f"\n提取出错：{e}")


def harvest_subtitles_interactive():
    """交互式：批量字幕收割"""
    print("\n批量字幕收割：只取 B 站自带（CC / AI）字幕，不下载视频、不做 OCR")
    target = input("请输入 UP 主空间 URL、视频 URL，或 urls.txt 清单路径：").strip()
    if not target:
        print("错误：不能为空")
        return

    cookie_manager = CookieManager()
    cookie_path = cookie_manager.cookie_path if cookie_manager.is_logged_in() else None

    output_dir = input("请输入输出目录 (直接回车使用默认 'downloads'): ").strip() or "downloads"

    max_str = input("最多处理多少个视频 (直接回车=不限): ").strip()
    max_count = int(max_str) if max_str.isdigit() else 0

    probe_only = input("只探测命中率、不落盘？(y/n, 默认 n): ").strip().lower() == 'y'

    harvester = SubtitleHarvester(
        output_dir=output_dir,
        cookie_path=cookie_path,
        state_path=os.path.join(output_dir, "harvest_state.json"),
        out_format="jsonl",
    )
    try:
        harvester.run(target=target, max_count=max_count, probe_only=probe_only)
    except Exception as e:
        print(f"\n收割出错：{e}")


def configure_settings():
    """配置设置"""
    print("\n配置选项:")
    print("1. 设置 Cookie（手动粘贴）")
    print("2. 查看 Cookie 状态（联网校验）")
    print("3. 清除 Cookie")
    choice = input("请输入选项 (1-3): ").strip()
    
    cookie_manager = CookieManager()
    
    if choice == "1":
        # ⚠️ 自动获取已屏蔽（需要弹浏览器手动登录，无界面环境不可用）。
        #    这里只保留最可靠的一条路：手动粘贴 Cookie 字符串。
        print("\n【手动粘贴 Cookie】")
        print("获取方法：浏览器登录 B 站 → F12 → Network → 任意请求 → 复制请求头 Cookie")
        print("（至少要有 SESSDATA；建议连 bili_jct、DedeUserID 一起带上）")
        cookie_str = input("\n请粘贴 Cookie 字符串（形如 SESSDATA=xxx; bili_jct=yyy）: ").strip()
        if not cookie_str:
            print("Cookie 不能为空，已取消")
        else:
            # 真正写入 Cookie 文件
            cookie_manager.set_cookie(cookie_str)
            # 写完**立刻联网验证** —— 否则用户不知道到底成没成，
            # 而「文件里有字符串」并不等于「服务端认」（Cookie 可能已过期）
            print("\n正在联网校验…")
            ok_flag, message = cookie_manager.check_cookie_online()
            print(f"{'✓' if ok_flag else '✗'} {message}")
            if not ok_flag:
                print("\n⚠️ 校验没通过。常见原因：")
                print("   - 复制漏了 SESSDATA（它是关键字段）")
                print("   - 复制的是**未登录状态**下的 Cookie")
                print("   - Cookie 已经过期")
                print("   可以再选一次 1 重新粘贴。")
    elif choice == "2":
        # ⚠️ 必须用**联网检查**，不能只靠 is_logged_in()。
        #    is_logged_in() 只查文件里有没有 `SESSDATA` 字符串 ——
        #    Cookie 过期后它照样返回 True，用户会看到「已设置且有效」
        #    却在下载时处处失败（服务端其实返回 -101 未登录）。
        ok_flag, message = cookie_manager.check_cookie_online()
        print(f"\n{'✓' if ok_flag else '✗'} {message}")
        if not ok_flag:
            print("\n（文件里确实有 SESSDATA，但服务端不认 —— 这就是「看起来配了")
            print("  其实是失效的」那种情况。请重新设置 Cookie：选 1，手动粘贴。）")
    elif choice == "3":
        cookie_manager.clear_cookie()
        print("Cookie 已清除")


def main():
    """主函数"""
    print_banner()
    
    # 检查 Cookie
    cookie_manager = CookieManager()
    if not cookie_manager.is_logged_in():
        print("\n警告：未设置 Cookie，某些功能可能无法使用")
        print("建议先配置 Cookie (选项 6)")
    
    while True:
        print_menu()
        choice = get_user_choice()
        
        if choice == "1":
            download_single_video()
        elif choice == "2":
            download_playlist()
        elif choice == "3":
            search_and_download()
        elif choice == "4":
            download_channel()
        elif choice == "5":
            extract_subtitle()
        elif choice == "6":
            configure_settings()
        elif choice == "7":
            harvest_subtitles_interactive()
        elif choice == "8":
            print("\n感谢使用，再见!")
            break
        else:
            print("\n无效的选项，请重新选择")
        
        # 询问是否继续
        if choice != "8":
            cont = input("\n按回车继续，输入 q 退出：").strip().lower()
            if cont == "q":
                print("\n感谢使用，再见!")
                break


def main_cli(args: Optional[list] = None):
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="Bilibili 视频/音频下载器",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "url",
        nargs="?",
        help="视频 URL"
    )
    
    parser.add_argument(
        "-o", "--output",
        default="downloads",
        help="输出目录 (默认：downloads)"
    )
    
    parser.add_argument(
        "-q", "--quality",
        choices=["best", "1080", "720", "480"],
        default="best",
        help="视频质量 (默认：best)"
    )
    
    parser.add_argument(
        "-a", "--audio", "--audio-only",
        dest="audio_only",
        action="store_true",
        help="仅下载音频"
    )
    
    parser.add_argument(
        "-s", "--sub", "--subtitle",
        dest="subtitle",
        action="store_true",
        help="下载字幕（同时下载视频）"
    )
    
    parser.add_argument(
        "--subtitle-only",
        action="store_true",
        help="仅下载字幕（不下载视频）"
    )
    
    parser.add_argument(
        "--format",
        choices=["text", "srt"],
        default="text",
        help="字幕输出格式 (默认：text)"
    )
    
    parser.add_argument(
        "-p", "--playlist",
        action="store_true",
        help="下载播放列表"
    )
    
    parser.add_argument(
        "--channel",
        help="频道/UP 主空间 URL"
    )
    
    parser.add_argument(
        "--search",
        help="搜索关键词"
    )
    
    parser.add_argument(
        "--max",
        type=int,
        default=10,
        help="频道/搜索最多下载数量 (默认：10；必须 ≥1)"
    )

    parser.add_argument(
        "--max-count",
        type=int,
        default=0,
        help="收割/播放列表最多处理多少个视频 (0=不限；不给则沿用 --max)"
    )

    parser.add_argument(
        "--harvest",
        metavar="URL|文件",
        help="批量字幕收割：UP 主空间 URL、视频 URL，或一份 urls.txt 清单。"
             "只取 B 站自带字幕，不下载视频、不做 OCR"
    )

    parser.add_argument(
        "--urls-file",
        metavar="文件",
        help="配合 --harvest：一行一个 URL 的清单文件（# 开头为注释）"
    )

    parser.add_argument(
        "--state",
        default=None,
        help="收割断点状态文件 (默认：<输出目录>/harvest_state.json)"
    )

    parser.add_argument(
        "--harvest-format",
        choices=["jsonl", "txt"],
        default="jsonl",
        help="收割输出格式：jsonl=汇总成一句一行的结构化文件（默认）；txt=一个视频一个文本文件"
    )

    parser.add_argument(
        "--rate-limit",
        type=float,
        default=1.0,
        help="收割时每个命中视频之间的间隔秒数 (默认：1.0)"
    )

    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="只探测有没有字幕（不取正文、不落盘），用来评估命中率"
    )

    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="收割时重试上次失败的条目（「无字幕」默认不重试）"
    )
    
    parser.add_argument(
        "--order",
        default="pubdate",
        help="排序方式。⚠️ 频道与搜索的**可选值不同**："
             "频道 pubdate/latest/hot/view；搜索 totalrank/click/pubdate/stow。"
             "填错会直接报错（不再静默降级）"
    )
    
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="进入交互式模式"
    )
    
    parser.add_argument(
        "--cookie",
        help="Bilibili Cookie"
    )

    parser.add_argument(
        "--check-cookie",
        dest="check_cookie",
        action="store_true",
        help="联网检查 Cookie 是否还有效，然后退出（不下载任何东西）"
    )

    # ---- 下载行为微调（原来这些全硬编码在 downloader.py 里，用户改不了）----
    tuning = parser.add_argument_group("下载行为（反爬 / 重试 / 断点续传）")
    tuning.add_argument(
        "--no-resume", dest="no_resume", action="store_true",
        help="关闭断点续传（默认开启：大文件中断后重跑从断点继续）"
    )
    tuning.add_argument(
        "--sleep-interval", dest="sleep_interval", type=float, default=3.0,
        help="每次请求之间的最小间隔秒数（默认 3）"
    )
    tuning.add_argument(
        "--max-sleep-interval", dest="max_sleep_interval", type=float, default=10.0,
        help="间隔的随机上限（默认 10；yt-dlp 在两者之间随机取）"
    )
    tuning.add_argument(
        "--retries", type=int, default=3,
        help="单个视频的重试次数（默认 3）"
    )
    tuning.add_argument(
        "--retry-sleep", dest="retry_sleep", type=float, default=5.0,
        help="重试前的等待秒数（默认 5）"
    )
    tuning.add_argument(
        "--audio-quality", dest="audio_quality", default="0",
        help="音频质量，0 最好、9 最差（默认 0）"
    )
    tuning.add_argument(
        "--merge-format", dest="merge_format", default="mp4",
        help="音视频合并后的容器格式（默认 mp4）"
    )
    tuning.add_argument(
        "--retry-downloads", dest="retry_downloads", action="store_true",
        help="只重试**上次下载失败的**那些（读失败清单，成功会自动移除）。"
             "注意与 --retry-failed 不同：那个是重试「字幕收割」失败的条目"
    )
    tuning.add_argument(
        "--failure-list", dest="failure_list", default=None,
        help="下载失败清单文件路径（默认 <输出目录>/failed_downloads.json）"
    )
    tuning.add_argument(
        "--no-info-json", dest="no_info_json", action="store_true",
        help="不写 .info.json（默认写：含发布时间 / 分P 标题 / 简介）"
    )
    tuning.add_argument(
        "--danmaku", dest="danmaku", action="store_true",
        help="同时下载弹幕（默认关。弹幕是观众发言，属辅助信息而非训练语料）"
    )
    tuning.add_argument(
        "--audio-format", dest="audio_format", default="mp3",
        choices=["mp3", "m4a", "opus", "wav", "flac"],
        help="音频格式（默认 mp3）"
    )
    tuning.add_argument(
        "--video-only", dest="video_only", action="store_true",
        help="只要画面不要声音（做 OCR / 视觉任务时有用）"
    )
    tuning.add_argument(
        "--ffmpeg-location", dest="ffmpeg_location", default=None,
        help="ffmpeg 可执行文件路径（默认自动探测：PATH → 本 venv 的 "
             "imageio_ffmpeg 副本）。合并音视频必需"
    )
    
    parser.add_argument(
        "--no-check-certificate",
        dest="no_check_certificate",
        action="store_true",
        help="跳过 HTTPS 证书校验（默认校验）"
    )
    
    parser.add_argument(
        "-v", "--version",
        action="store_true",
        help="显示版本信息"
    )
    
    parsed_args = parser.parse_args(args)
    
    # 显示版本信息
    if parsed_args.version:
        print(f"Bilibili 视频/音频下载器 v{__version__}")
        return
    
    # ---- 参数校验：非法值**直接报错退出**，不静默兜底 ----
    # ⚠️ 静默兜底比报错难排查得多：用户以为按 `--max 50` 下了 50 个，
    #    实际因为传了 -1 而一个都没下到，却不知道为什么。
    if parsed_args.max is not None and parsed_args.max < 1:
        print(f"❌ --max 必须 ≥1，实际给了 {parsed_args.max}", file=sys.stderr)
        print("   它表示「最多下载多少个」（要「不限」请用 --max-count 0）")
        return 2
    if parsed_args.max_count is not None and parsed_args.max_count < 0:
        print(f"❌ --max-count 不能为负，实际给了 {parsed_args.max_count}"
              f"（0 表示不限）", file=sys.stderr)
        return 2
    if parsed_args.order:
        # order 的合法值取决于目标是频道还是搜索，这里只做「是否已知」的粗筛；
        # 具体到某个模块的校验由 resolve_channel_order / resolve_search_order 负责
        known = set(CHANNEL_ORDERS) | set(SEARCH_ORDERS)
        if parsed_args.order not in known:
            print(f"❌ --order 不认识的排序方式：{parsed_args.order!r}", file=sys.stderr)
            print(f"   频道可用：{'/'.join(CHANNEL_ORDERS)}", file=sys.stderr)
            print(f"   搜索可用：{'/'.join(SEARCH_ORDERS)}", file=sys.stderr)
            return 2
    
    verify_ssl = not parsed_args.no_check_certificate
    
    # Cookie 有效性预检：联网问一次服务端，检查完即退出。
    # ⚠️ 不能用 is_logged_in() 代替 —— 那只查文件里有没有 SESSDATA 字符串，
    #    而 Cookie 过期后 B 站接口**不报错**，只会静默返回未登录结果。
    if parsed_args.check_cookie:
        cookie_manager = CookieManager()
        ok_flag, message = cookie_manager.check_cookie_online()
        print(f"{'✓' if ok_flag else '✗'} {message}")
        if not ok_flag:
            print("\n提示：Cookie 过期/无效时，B 站接口不会报错，只会静默返回未登录"
                  "结果，")
            print("      表现为「字幕全都探测不到」「视频信息取不到」—— 看起来像"
                  "风控或接口改版。")
            print("      重新获取：python -m bilibili_downloader --interactive")
        return 0 if ok_flag else 1
    
    # 设置 Cookie
    if parsed_args.cookie:
        cookie_manager = CookieManager()
        cookie_manager.set_cookie(parsed_args.cookie)
    
    # 显式交互模式，或未提供任何任务参数时进入交互模式
    # ⚠️ `retry_downloads` 必须在这里 —— 否则敲了 `--retry-downloads`
    #    会被判定成「没给任务」→ 掉进交互式问答（明明给了参数却被问「请输入 URL」）
    if parsed_args.interactive or not (
        parsed_args.url or parsed_args.channel or parsed_args.search
        or parsed_args.harvest or parsed_args.urls_file
        or parsed_args.retry_downloads
    ):
        main()
        return
    
    # 下载/提取
    cookie_manager = CookieManager()
    cookie_path = cookie_manager.cookie_path if cookie_manager.is_logged_in() else None
    downloader = BilibiliDownloader(
        cookie_path=cookie_path, verify_ssl=verify_ssl,
        # 这些原来全是硬编码，用户改不了；现在可从命令行覆盖，默认值不变
        resume=not parsed_args.no_resume,
        sleep_interval=parsed_args.sleep_interval,
        max_sleep_interval=parsed_args.max_sleep_interval,
        retries=parsed_args.retries,
        retry_sleep=parsed_args.retry_sleep,
        audio_quality=parsed_args.audio_quality,
        merge_output_format=parsed_args.merge_format,
        failure_path=(parsed_args.failure_list
                      or os.path.join(parsed_args.output, "failed_downloads.json")),
        write_info_json=not parsed_args.no_info_json,
        download_danmaku=parsed_args.danmaku,
        audio_format=parsed_args.audio_format,
        video_only=parsed_args.video_only,
        ffmpeg_path=parsed_args.ffmpeg_location,
    )

    # `--retry-downloads`：把「上次下载失败的」变成本次的目标列表。
    # 这样重跑不用重新枚举整个 UP 主空间，直接补没下的那几个。
    if parsed_args.retry_downloads:
        pending = downloader.failures.urls() if downloader.failures else []
        if not pending:
            print("✓ 失败清单是空的 —— 没有需要重试的")
            return 0
        print(f"从失败清单读到 {len(pending)} 个待重试：")
        for u in pending[:10]:
            print(f"  - {u}")
        if len(pending) > 10:
            print(f"  …另有 {len(pending) - 10} 个")
        ok_count = 0
        for i, u in enumerate(pending, 1):
            print(f"\n[{i}/{len(pending)}] {u}")
            result = downloader.download_video(u)
            if result:
                ok_count += 1
        left = len(downloader.failures) if downloader.failures else 0
        print("\n" + "=" * 52)
        print(f"重试完成：成功 {ok_count}，仍失败 {left}")
        if left:
            print("  失败的仍在清单里，可再次 --retry-downloads")
        return 0
    extractor = SubtitleExtractor(cookie_path=cookie_path, verify_ssl=verify_ssl)
    
    try:
        # 批量字幕收割（最优先：它既不下载视频也不做识别，成本最低）
        if parsed_args.harvest or parsed_args.urls_file:
            state_path = parsed_args.state or os.path.join(
                parsed_args.output, "harvest_state.json"
            )
            harvester = SubtitleHarvester(
                output_dir=parsed_args.output,
                cookie_path=cookie_path,
                verify_ssl=verify_ssl,
                state_path=state_path,
                rate_limit=parsed_args.rate_limit,
                out_format=parsed_args.harvest_format,
            )
            harvester.run(
                target=parsed_args.harvest,
                urls_file=parsed_args.urls_file,
                max_count=parsed_args.max_count,
                order=parsed_args.order,
                probe_only=parsed_args.probe_only,
                retry_failed=parsed_args.retry_failed,
            )
            return

        # 频道/UP 主空间下载
        if parsed_args.channel:
            channel_downloader = ChannelDownloader(
                output_dir=parsed_args.output,
                cookie_path=cookie_path
            )
            results = channel_downloader.download_by_url(
                parsed_args.channel,
                max_videos=parsed_args.max_count or parsed_args.max,
                audio_only=parsed_args.audio_only,
                order=parsed_args.order
            )
            print(f"下载完成！共下载 {len(results)} 个视频")
            # ⚠️ 一个都没下到 = 失败，必须返回非 0（否则 CI 无法感知）
            if not results:
                print("下载失败：没有成功下载任何视频")
                return 1
            return
        
        # 搜索并下载
        if parsed_args.search:
            search_downloader = SearchDownloader(
                output_dir=parsed_args.output,
                cookie_path=cookie_path,
                verify_ssl=verify_ssl
            )
            results = search_downloader.download_search_results(
                parsed_args.search,
                max_videos=parsed_args.max,
                order=parsed_args.order,
                audio_only=parsed_args.audio_only
            )
            print(f"下载完成！共下载 {len(results)} 个视频")
            if not results:
                print("下载失败：没有成功下载任何视频")
                return 1
            return
        
        # 仅下载字幕
        if parsed_args.subtitle_only:
            print("正在下载 AI 字幕...")
            subtitle_file = extractor.extract_subtitle(
                parsed_args.url,
                output_dir=parsed_args.output,
                output_format=parsed_args.format
            )
            if subtitle_file:
                print(f"字幕已保存：{subtitle_file}")
            else:
                print("该视频没有 AI 字幕")
            return
        
        if parsed_args.playlist:
            results = downloader.download_playlist(
                parsed_args.url,
                output_dir=parsed_args.output,
                # 合集动辄几十上百个视频，默认只下 10 个明显不够：
                # 用 --max-count 50 之类的值覆盖它（0 或不给则沿用 --max）
                max_videos=parsed_args.max_count or parsed_args.max
            )
            print(f"下载完成！共下载 {len(results)} 个视频")
            if not results:
                print("下载失败：合集里没有成功下载任何视频")
                return 1
        elif parsed_args.audio_only:
            result = downloader.download_audio(
                parsed_args.url,
                output_dir=parsed_args.output
            )
            if result:
                print(f"下载完成！文件：{result}")
            else:
                print("下载失败")
                return 1
        else:
            result = downloader.download_video(
                parsed_args.url,
                output_dir=parsed_args.output,
                quality=parsed_args.quality
            )
            if result:
                print(f"下载完成！文件：{result}")
            else:
                # ⚠️ 必须返回非 0：之前这里只 print 然后隐式 return None，
                #    退出码恒为 0，脚本 / CI 无法用 $? 判断成败。
                print("下载失败")
                return 1
        
        # 下载字幕
        if parsed_args.subtitle:
            subtitle_file = extractor.extract_subtitle(
                parsed_args.url,
                output_dir=parsed_args.output,
                output_format=parsed_args.format
            )
            if subtitle_file:
                print(f"字幕已保存：{subtitle_file}")
    except Exception as e:
        print(f"错误：{e}")
        sys.exit(1)


if __name__ == "__main__":
    # 有命令行参数时走 CLI，无参数时进入交互模式
    if len(sys.argv) > 1:
        main_cli()
    else:
        main()
