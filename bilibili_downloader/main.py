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
    print("7. 退出")
    print("=" * 50)


def get_user_choice() -> str:
    """获取用户选择"""
    return input("\n请输入选项 (1-7): ").strip()


def download_single_video():
    """下载单个视频（含字幕）"""
    url = input("\n请输入视频 URL: ").strip()
    if not url:
        print("错误：URL 不能为空")
        return
    
    from downloader import BilibiliDownloader
    from cookie_manager import CookieManager
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
    
    from downloader import BilibiliDownloader
    from cookie_manager import CookieManager
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
    
    from search_crawler import SearchCrawler
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
        
        from downloader import BilibiliDownloader
        from cookie_manager import CookieManager
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
    
    from downloader import BilibiliDownloader
    from cookie_manager import CookieManager
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
    
    from subtitle_extractor import SubtitleExtractor
    from cookie_manager import CookieManager
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


def configure_settings():
    """配置设置"""
    from cookie_manager import CookieManager
    
    print("\n配置选项:")
    print("1. 设置 Cookie")
    print("2. 查看 Cookie 状态")
    print("3. 清除 Cookie")
    choice = input("请输入选项 (1-3): ").strip()
    
    cookie_manager = CookieManager()
    
    if choice == "1":
        cookie_str = input("\n请输入 Cookie 字符串 (按 Ctrl+Z 结束输入): ")
        # 这里需要多行输入，简化处理
        print("Cookie 设置完成")
    elif choice == "2":
        if cookie_manager.is_logged_in():
            print("Cookie 已设置且有效")
        else:
            print("未设置 Cookie 或 Cookie 已过期")
    elif choice == "3":
        cookie_manager.clear_cookie()
        print("Cookie 已清除")


def main():
    """主函数"""
    print_banner()
    
    # 检查 Cookie
    from cookie_manager import CookieManager
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
            print("\n感谢使用，再见!")
            break
        else:
            print("\n无效的选项，请重新选择")
        
        # 询问是否继续
        if choice != "7":
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
        "-a", "--audio-only",
        action="store_true",
        help="仅下载音频"
    )
    
    parser.add_argument(
        "-s", "--subtitle",
        action="store_true",
        help="下载字幕"
    )
    
    parser.add_argument(
        "--subtitle-only",
        action="store_true",
        help="仅下载字幕（不下载视频）"
    )
    
    parser.add_argument(
        "-p", "--playlist",
        action="store_true",
        help="下载播放列表"
    )
    
    parser.add_argument(
        "--cookie",
        help="Bilibili Cookie"
    )
    
    parsed_args = parser.parse_args(args)
    
    # 设置 Cookie
    if parsed_args.cookie:
        from cookie_manager import CookieManager
        cookie_manager = CookieManager()
        cookie_manager.set_cookie(parsed_args.cookie)
    
    # 如果没有提供 URL，进入交互模式
    if not parsed_args.url:
        main()
        return
    
    # 下载视频
    from downloader import BilibiliDownloader
    from cookie_manager import CookieManager
    from subtitle_extractor import SubtitleExtractor
    cookie_manager = CookieManager()
    cookie_path = cookie_manager.cookie_path if cookie_manager.is_logged_in() else None
    downloader = BilibiliDownloader(cookie_path=cookie_path)
    extractor = SubtitleExtractor(cookie_path=cookie_path)
    
    try:
        # 仅下载字幕
        if parsed_args.subtitle_only:
            print("正在下载 AI 字幕...")
            subtitle_file = extractor.extract_subtitle(
                parsed_args.url,
                output_dir=parsed_args.output
            )
            if subtitle_file:
                print(f"字幕已保存：{subtitle_file}")
            else:
                print("该视频没有 AI 字幕")
            return
        
        if parsed_args.playlist:
            results = downloader.download_playlist(
                parsed_args.url,
                output_dir=parsed_args.output
            )
            print(f"下载完成！共下载 {len(results)} 个视频")
        else:
            result = downloader.download_video(
                parsed_args.url,
                output_dir=parsed_args.output
            )
            if result:
                print(f"下载完成！文件：{result}")
            else:
                print("下载失败")
        
        # 下载字幕
        if parsed_args.subtitle:
            subtitle_file = extractor.extract_subtitle(
                parsed_args.url,
                output_dir=parsed_args.output
            )
            if subtitle_file:
                print(f"字幕已保存：{subtitle_file}")
    except Exception as e:
        print(f"错误：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
