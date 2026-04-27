#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
核心下载器模块
基于 yt-dlp 实现 Bilibili 视频/音频下载
"""

import os
import re
import time
import json
import subprocess
import threading
from typing import Optional, List, Dict, Any, Callable
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime


@dataclass
class VideoInfo:
    """视频信息数据类"""
    video_id: str
    title: str
    author: str
    duration: int
    view_count: int
    like_count: int
    upload_date: str
    description: str
    thumbnail: str
    url: str
    chapters: Optional[List[Dict]] = None


class BilibiliDownloader:
    """Bilibili 视频下载器（基于 yt-dlp）"""
    
    def __init__(self, output_dir: str = "downloads", cookie_path: str = None):
        """
        :param output_dir: 下载输出目录
        :param cookie_path: Cookie 文件路径
        """
        self.output_dir = output_dir
        self.cookie_path = cookie_path
        self.ytdlp_path = self._find_ytdlp()
        
        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        
        # 下载进度回调
        self.progress_callback: Optional[Callable] = None
        self.status_callback: Optional[Callable] = None
    
    def _find_ytdlp(self) -> Optional[str]:
        """查找 yt-dlp 可执行文件"""
        # 尝试直接调用
        try:
            result = subprocess.run(
                ["yt-dlp", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return "yt-dlp"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        # 尝试 Python 模块调用
        try:
            result = subprocess.run(
                ["python", "-m", "yt_dlp", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return "python -m yt_dlp"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        return None
    
    def check_ytdlp(self) -> bool:
        """检查 yt-dlp 是否可用"""
        if self.ytdlp_path:
            return True
        
        print("yt-dlp 未安装，正在尝试安装...")
        try:
            subprocess.run(
                ["pip", "install", "-U", "yt-dlp"],
                check=True,
                capture_output=True
            )
            self.ytdlp_path = "python -m yt_dlp"
            print("✓ yt-dlp 安装成功")
            return True
        except subprocess.CalledProcessError as e:
            print(f"yt-dlp 安装失败：{e}")
            print("\n请手动安装 yt-dlp:")
            print("  pip install -U yt-dlp")
            print("\n或者从 https://github.com/yt-dlp/yt-dlp 下载")
            return False
    
    def _get_cookie_option(self) -> List[str]:
        """获取 Cookie 命令行选项"""
        if self.cookie_path and os.path.exists(self.cookie_path):
            return ["--cookies", self.cookie_path]
        return []
    
    def _build_download_args(self, video_url: str, output_path: str, 
                             format_str: str = "best", 
                             download_audio: bool = False,
                             download_subtitle: bool = False) -> List[str]:
        """构建 yt-dlp 下载参数"""
        args = []
        
        # 使用 Python 模块调用
        if self.ytdlp_path == "python -m yt_dlp":
            args = ["python", "-m", "yt_dlp"]
        else:
            args = ["yt-dlp"]
        
        # Cookie 选项
        args.extend(self._get_cookie_option())
        
        # 输出模板
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        output_template = output_path.replace('.mp4', '').replace('.mkv', '').replace('.webm', '')
        args.extend([
            "-o", f"{output_template}.%(ext)s",
            "--no-playlist"  # 不下载整个播放列表
        ])
        
        # 字幕选项
        if download_subtitle:
            args.extend([
                "--write-auto-sub",  # 下载自动生成的字幕
                "--sub-lang", "zh",  # 中文
                "--sub-format", "srt"  # SRT 格式
            ])
        
        # 格式选择
        if download_audio:
            args.extend([
                "-x",  # 提取音频
                "--audio-format", "mp3",
                "--audio-quality", "0"  # 最佳质量
            ])
        else:
            # 视频格式：优先 mp4，选择最佳质量
            args.extend([
                "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4"
            ])
        
        # 反爬策略
        args.extend([
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "--referer", "https://www.bilibili.com",
            "--sleep-interval", "3",  # 请求间隔 3 秒
            "--max-sleep-interval", "10",
            "--retries", "3",
            "--fragment-retries", "3",
            "--retry-sleep", "5",
            "--no-check-certificate",  # 跳过证书验证
            "--no-clean-info-json"
        ])
        
        # 添加 URL
        args.append(video_url)
        
        return args
    
    def download_video(self, video_url: str, title: str = None, 
                       output_dir: str = None, download_subtitle: bool = False,
                       force_subtitle: bool = False, quality: str = "best") -> Optional[str]:
        """
        下载单个视频
        :param video_url: 视频 URL
        :param title: 视频标题（用于命名）
        :param output_dir: 输出目录
        :param download_subtitle: 是否下载字幕
        :param force_subtitle: 是否强制使用 Playwright 提取字幕
        :param quality: 视频质量
        :return: 下载的文件路径，失败返回 None
        """
        if not self.check_ytdlp():
            return None
        
        output_dir = output_dir or self.output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        # 生成文件名
        if not title:
            title = self._extract_video_id(video_url)
        
        # 清理标题
        safe_title = self._sanitize_filename(title)
        output_path = os.path.join(output_dir, f"{safe_title}.mp4")
        
        # 检查是否已下载
        video_exists = os.path.exists(output_path)
        if video_exists:
            print(f"✓ 视频文件已存在：{safe_title}")
        else:
            print(f"正在下载：{safe_title}")
            
            # 构建命令
            args = self._build_download_args(video_url, output_path, 
                                           download_subtitle=False)  # 视频下载时不处理字幕
            
            try:
                # 执行下载
                process = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    errors='ignore'
                )
                
                # 实时输出进度
                for line in process.stdout:
                    print(line.strip())
                    if self.progress_callback:
                        self.progress_callback(line)
                
                process.wait()
                
                if process.returncode != 0:
                    print(f"✗ 下载失败，返回码：{process.returncode}")
                    return None
                    
            except Exception as e:
                print(f"✗ 下载异常：{e}")
                return None
        
        # 下载字幕（无论视频是否已存在）
        if download_subtitle:
            if force_subtitle:
                print("\n正在使用 Playwright 提取 AI 字幕...")
                subtitle_path = self._extract_subtitle_playwright(video_url, safe_title, output_dir)
            else:
                print("\n正在下载 AI 字幕...")
                subtitle_path = self._download_subtitle_ytdlp(video_url, safe_title, output_dir)
            
            if subtitle_path:
                print(f"✓ 字幕已保存：{subtitle_path}")
            else:
                print("⚠ 字幕下载失败")
        
        print(f"✓ 下载完成：{output_path}")
        return output_path
    
    def _download_subtitle_ytdlp(self, video_url: str, safe_title: str, 
                                  output_dir: str) -> Optional[str]:
        """使用 yt-dlp 下载字幕"""
        try:
            print(f"  使用 yt-dlp 下载字幕，输出目录：{output_dir}")
            
            args = ["python", "-m", "yt_dlp"]
            args.extend(self._get_cookie_option())
            # 字幕选项 - 尝试多种字幕语言
            args.extend([
                "--write-auto-sub",  # 下载自动生成的字幕
                "--write-sub",       # 下载字幕
                "--sub-lang", "zh-Hans",  # 简体中文
                "--sub-lang", "zh",      # 中文
                "--sub-lang", "zh-Hant", # 繁体中文
                "--sub-lang", "en",      # 英文
                "--skip-download",   # 只下载字幕，不下载视频
                "--convert-subs", "srt",  # 转换为 SRT 格式
                "-o", f"{output_dir}/{safe_title}.%(ext)s",
            ])
            # 添加 URL（放在最后）
            args.append(video_url)
            
            print(f"  命令：{' '.join(args[:5])} ... {video_url}")
            
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=120,
                encoding='utf-8',
                errors='ignore'
            )
            
            # 输出 yt-dlp 的调试信息
            if result.stderr:
                print("  yt-dlp 输出:")
                for line in result.stderr.split('\n')[-15:]:  # 显示最后 15 行
                    if line.strip():
                        print(f"    {line}")
            
            if result.returncode != 0:
                print(f"  yt-dlp 返回码：{result.returncode}")
            
            # 查找生成的字幕文件
            print(f"  搜索字幕文件：{output_dir}")
            subtitle_files = []
            if os.path.exists(output_dir):
                for f in os.listdir(output_dir):
                    if f.startswith(safe_title) and f.endswith(('.srt', '.vtt', '.ass')):
                        subtitle_files.append(f)
                        print(f"  找到字幕文件：{f}")
            
            if subtitle_files:
                # 返回第一个找到的字幕文件
                return os.path.join(output_dir, subtitle_files[0])
            
            print("  yt-dlp 未下载字幕，尝试使用 Playwright 提取...")
            return self._extract_subtitle_playwright(video_url, safe_title, output_dir)
            
        except subprocess.TimeoutExpired:
            print("  yt-dlp 下载字幕超时，尝试使用 Playwright 提取...")
            # 超时后尝试使用 Playwright 提取
            try:
                return self._extract_subtitle_playwright(video_url, safe_title, output_dir)
            except Exception as playwright_err:
                print(f"  Playwright 提取也失败：{playwright_err}")
                return None
        except Exception as e:
            print(f"  yt-dlp 下载字幕失败：{e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _extract_subtitle_playwright(self, video_url: str, safe_title: str, 
                                      output_dir: str) -> Optional[str]:
        """使用 Playwright 提取 AI 字幕"""
        try:
            from subtitle_extractor import SubtitleExtractor
            extractor = SubtitleExtractor(cookie_path=self.cookie_path, output_dir=output_dir)
            return extractor._extract_subtitle_playwright(video_url, safe_title, output_dir)
        except Exception as e:
            print(f"  Playwright 提取失败：{e}")
            return None
    
    def _extract_video_id(self, url: str) -> str:
        """从 URL 中提取视频 ID"""
        # Bilibili 视频 ID 模式
        patterns = [
            r'video/(BV\w+)',
            r'BV(\w+)',
            r'av(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        # 无法提取时使用时间戳
        return datetime.now().strftime("%Y%m%d_%H%M%S")
    
    def _sanitize_filename(self, filename: str) -> str:
        """清理文件名，移除非法字符"""
        # 移除 Windows 文件名非法字符
        illegal_chars = r'[<>:"/\\|？*]'
        filename = re.sub(illegal_chars, '', filename)
        # 限制长度
        if len(filename) > 100:
            filename = filename[:100]
        return filename.strip()
    
    def download_audio(self, video_url: str, title: str = None,
                       output_dir: str = None) -> Optional[str]:
        """
        下载音频
        :param video_url: 视频 URL
        :param title: 标题
        :param output_dir: 输出目录
        :return: 下载的文件路径
        """
        if not self.check_ytdlp():
            return None
        
        output_dir = output_dir or self.output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        if not title:
            title = self._extract_video_id(video_url)
        
        safe_title = self._sanitize_filename(title)
        output_path = os.path.join(output_dir, f"{safe_title}.mp3")
        
        if os.path.exists(output_path):
            print(f"✓ 音频文件已存在：{safe_title}")
            return output_path
        
        print(f"正在下载音频：{safe_title}")
        
        args = self._build_download_args(video_url, output_path, 
                                        download_audio=True)
        
        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            
            for line in process.stdout:
                print(line.strip())
            
            process.wait()
            
            if process.returncode == 0:
                print(f"✓ 音频下载完成：{output_path}")
                return output_path
            else:
                print(f"✗ 下载失败")
                return None
                
        except Exception as e:
            print(f"✗ 下载异常：{e}")
            return None
    
    def download_playlist(self, playlist_url: str, output_dir: str = None,
                          max_videos: int = 10) -> List[str]:
        """
        下载播放列表/合集
        :param playlist_url: 播放列表 URL
        :param output_dir: 输出目录
        :param max_videos: 最大下载数量
        :return: 下载的文件路径列表
        """
        output_dir = output_dir or self.output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        # 使用 yt-dlp 下载播放列表
        args = ["python", "-m", "yt_dlp"]
        args.extend(self._get_cookie_option())
        args.extend([
            "-o", f"{output_dir}/%(title)s.%(ext)s",
            "--yes-playlist",
            "--playlist-end", str(max_videos),
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        ])
        args.append(playlist_url)
        
        downloaded = []
        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            
            for line in process.stdout:
                print(line.strip())
            
            process.wait()
            
            if process.returncode == 0:
                print("✓ 播放列表下载完成")
                # 列出下载的文件
                if os.path.exists(output_dir):
                    for f in os.listdir(output_dir):
                        if f.endswith(('.mp4', '.mkv', '.webm')):
                            downloaded.append(os.path.join(output_dir, f))
            else:
                print("✗ 播放列表下载失败")
                
        except Exception as e:
            print(f"✗ 下载异常：{e}")
        
        return downloaded
    
    def download_up_videos(self, up_url: str, output_dir: str = None,
                           max_videos: int = 10) -> List[str]:
        """
        下载 UP 主空间视频
        :param up_url: UP 主空间 URL
        :param output_dir: 输出目录
        :param max_videos: 最大下载数量
        :return: 下载的文件路径列表
        """
        from channel_crawler import ChannelDownloader
        downloader = ChannelDownloader(output_dir or self.output_dir, self.cookie_path)
        return downloader.download_by_url(up_url, max_videos=max_videos)
    
    def download_channel(self, channel_url: str, output_dir: str = None,
                         max_videos: int = 10) -> List[str]:
        """
        下载频道视频
        :param channel_url: 频道 URL
        :param output_dir: 输出目录
        :param max_videos: 最大下载数量
        :return: 下载的文件路径列表
        """
        return self.download_up_videos(channel_url, output_dir, max_videos)


class AsyncDownloader(BilibiliDownloader):
    """异步下载器（支持批量下载）"""
    
    def __init__(self, output_dir: str = "downloads", cookie_path: str = None,
                 max_concurrent: int = 3):
        """
        :param output_dir: 输出目录
        :param cookie_path: Cookie 路径
        :param max_concurrent: 最大并发数
        """
        super().__init__(output_dir, cookie_path)
        self.max_concurrent = max_concurrent
        self._lock = threading.Lock()
        self._active_downloads = 0
    
    def download_batch(self, video_urls: List[str], audio_only: bool = False,
                       download_subtitle: bool = False) -> List[str]:
        """
        批量下载视频
        :param video_urls: 视频 URL 列表
        :param audio_only: 是否只下载音频
        :param download_subtitle: 是否下载字幕
        :return: 下载的文件路径列表
        """
        downloaded = []
        threads = []
        
        def download_with_semaphore(url):
            with self._lock:
                while self._active_downloads >= self.max_concurrent:
                    time.sleep(0.5)
                self._active_downloads += 1
            
            try:
                if audio_only:
                    result = self.download_audio(url)
                else:
                    result = self.download_video(url, download_subtitle=download_subtitle)
                
                if result:
                    downloaded.append(result)
            finally:
                with self._lock:
                    self._active_downloads -= 1
        
        for url in video_urls:
            t = threading.Thread(target=download_with_semaphore, args=(url,))
            threads.append(t)
            t.start()
            
            # 控制并发
            if len(threads) >= self.max_concurrent:
                for t in threads:
                    t.join()
                threads = []
        
        # 等待剩余下载完成
        for t in threads:
            t.join()
        
        return downloaded
