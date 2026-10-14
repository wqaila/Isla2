#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
核心下载器模块
基于 yt-dlp 实现 Bilibili 视频/音频下载
"""

import json
import os
import re
import subprocess
import threading
import concurrent.futures
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass
from datetime import datetime

try:
    from .download_failures import FailureStore
except ImportError:  # 直接以脚本方式运行本文件时回退
    from download_failures import FailureStore

# yt-dlp 自动安装的超时（秒）。没有超时的话网络卡住会无限挂起 ——
# 实测踩过：沙箱里挂死了 3 个后台 job，连 kill 都要等。
YTDLP_INSTALL_TIMEOUT = 180


def parse_info_json(path: Optional[str]) -> Optional[Dict]:
    """解析 yt-dlp 写出的 `.info.json`，取出对语料有用的元数据。

    【为什么需要它】
    `_build_download_args` 一直带着 `--no-clean-info-json`（即**写** info.json），
    但**从来没人读** —— 数据一直躺在本地没人用。里面有几样对下游有价值：

    - `upload_date`：做时间线语料时能按时间排序/筛选
    - `chapters` / 分P 标题：区分同一 BV 的不同段落
    - `description`：简介里常出现角色名与设定，可作为术语白名单线索

    :return: 解析好的字典；文件不存在/坏 JSON/None 一律返回 None（不抛异常）
    """
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        # 坏文件不能中断流程：info.json 只是附带的元数据
        print(f"  ⚠️ info.json 解析失败（已跳过）：{e}")
        return None

    if not isinstance(data, dict):
        return None

    upload_date = str(data.get("upload_date") or "")
    readable = ""
    if len(upload_date) == 8 and upload_date.isdigit():
        readable = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    chapters = []
    for ch in (data.get("chapters") or []):
        if isinstance(ch, dict) and ch.get("title"):
            chapters.append({"title": ch["title"],
                             "start": ch.get("start_time")})

    return {
        "video_id": data.get("id") or "",
        "title": data.get("title") or "",
        "uploader": data.get("uploader") or "",
        "upload_date": upload_date,
        "date": readable,
        "duration": data.get("duration"),
        "description": data.get("description") or "",
        "chapters": chapters,
    }


def parse_danmaku_xml(path: Optional[str]) -> List[Dict]:
    """解析 B 站弹幕 XML，返回 `[{"time","text","color","mode"}]`。

    【定位说明：弹幕是**辅助信息**，不是训练语料】
    弹幕是**观众**发言，不是角色台词 —— 直接拿来训 `zjy7` 是错的
    （会把观众的语气学进角色里）。它真正的价值是：
    - 观众对内容的即时反应，可用来理解「这个角色受欢迎吗」
    - 弹幕里高频出现的角色名/梗，可作为 `term_fix.py` 的**错字线索**

    格式：`<d p="时间,模式,字号,颜色,时间戳,池,用户,行ID">文本</d>`
    """
    if not path or not os.path.exists(path):
        return []
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(path)
    except Exception as e:
        print(f"  ⚠️ 弹幕 XML 解析失败（已跳过）：{e}")
        return []

    items: List[Dict] = []
    for node in tree.iter("d"):
        p = node.get("p") or ""
        parts = p.split(",")
        if len(parts) < 4:
            continue                       # 坏行跳过，不整份丢弃
        try:
            time_val = float(parts[0])
            color = int(parts[3])
        except (ValueError, TypeError):
            continue
        text = (node.text or "").strip()
        if not text:
            continue
        items.append({
            "time": time_val,
            "text": text,
            "color": color,
            "mode": parts[1] if len(parts) > 1 else "",
        })
    return items


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
    
    def __init__(self, output_dir: str = "downloads", cookie_path: str = None,
                 verify_ssl: bool = True, resume: bool = True,
                 sleep_interval: float = 3, max_sleep_interval: float = 10,
                 retries: int = 3, fragment_retries: int = 3,
                 retry_sleep: float = 5, audio_quality: str = "0",
                 merge_output_format: str = "mp4",
                 failure_path: str = None,
                 write_info_json: bool = True,
                 download_danmaku: bool = False,
                 audio_format: str = "mp3",
                 video_only: bool = False):
        """
        :param output_dir: 下载输出目录
        :param cookie_path: Cookie 文件路径
        :param verify_ssl: 是否校验 HTTPS 证书（默认校验，仅在明确要求时关闭）
        :param resume: 断点续传（默认开）。大文件下到一半中断后，
            重跑会**从断点继续**而不是从头下。极少数需要「重新下干净的」
            场景可以关掉
        :param sleep_interval: 每次请求之间的最小间隔秒数（反爬）
        :param max_sleep_interval: 间隔的随机上限（yt-dlp 会在
            [sleep_interval, max_sleep_interval] 之间随机取值）
        :param retries: 单个视频的重试次数
        :param fragment_retries: 分片（DASH 流）的重试次数
        :param retry_sleep: 重试前的等待秒数
        :param audio_quality: 音频质量（0 = 最好，9 = 最差）
        :param merge_output_format: 音视频合并后的容器格式
        :param failure_path: 失败清单文件路径（None 表示不记录）。
            批量下载时传入它，就能在重跑时用 `--retry-failed` 精确补齐
        :param write_info_json: 是否写 `.info.json`（默认写）。里面有发布时间、
            分P 标题、简介 —— 对做时间线语料与术语白名单有用
        :param download_danmaku: 是否下载弹幕（**默认关**）。
            弹幕是**观众**发言、不是角色台词，定位是辅助信息而非训练语料；
            且体积可能很大，所以默认不开
        :param audio_format: 音频格式（默认 mp3；也可 m4a/opus/wav）
        :param video_only: 只要画面不要声音（做 OCR / 视觉任务时有用）

        ⚠️ 这些参数**默认值与改动前完全一致**（纯增量：原来全硬编码，
        用户改不了；现在可配但仍保持原行为）。
        """
        self.output_dir = output_dir
        self.cookie_path = cookie_path
        self.verify_ssl = verify_ssl
        self.resume = resume
        self.sleep_interval = sleep_interval
        self.max_sleep_interval = max_sleep_interval
        self.retries = retries
        self.fragment_retries = fragment_retries
        self.retry_sleep = retry_sleep
        self.audio_quality = audio_quality
        self.merge_output_format = merge_output_format
        # 失败清单（None = 不记录，保持旧行为）
        self.failures = FailureStore(failure_path) if failure_path else None
        self.write_info_json = write_info_json
        self.download_danmaku = download_danmaku
        self.audio_format = audio_format
        self.video_only = video_only
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
        """检查 yt-dlp 是否可用（缺失时尝试自动安装，但**必须带超时**）。

        ⚠️ 2026-10-12 踩过：原来这里的 `subprocess.run(["pip", "install", ...])`
        **没有 timeout**，网络卡住时会**无限挂起** ——
        表现为「脚本没有任何输出、也永远不结束」。
        所以现在带上超时，并且失败时给可执行的指引而不是干等。
        """
        if self.ytdlp_path:
            return True

        print("yt-dlp 未安装，正在尝试安装（最多等 180 秒）...")
        try:
            subprocess.run(
                ["pip", "install", "-U", "yt-dlp"],
                check=True,
                capture_output=True,
                timeout=YTDLP_INSTALL_TIMEOUT,
            )
            self.ytdlp_path = "python -m yt_dlp"
            print("✓ yt-dlp 安装成功")
            return True
        except subprocess.TimeoutExpired:
            print(f"✗ 安装超时（超过 {YTDLP_INSTALL_TIMEOUT} 秒），已中止")
            print("  可能是网络不通或 pip 源太慢。建议手动安装：")
            print("    pip install -U yt-dlp")
            return False
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
                             quality: str = "best", 
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
                "--audio-format", str(self.audio_format),
                "--audio-quality", str(self.audio_quality)
            ])
        else:
            # 画质映射：把档位名换成 yt-dlp 的 format 选择器。
            # ⚠️ 档位要覆盖到 4K/2K —— 原来只有 best/1080/720/480，
            #    想要 1440 或 2160 的人只能拿 best（可能下到很低画质）。
            # `video_only` 时去掉 `+bestaudio`，只留画面（OCR/视觉任务用）。
            heights = ("2160", "1440", "1080", "720", "480", "360")
            quality_format_map = {
                "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            }
            for h in heights:
                if self.video_only:
                    quality_format_map[h] = (
                        f"bestvideo[height<={h}][ext=mp4]/best[height<={h}][ext=mp4]"
                        f"/bestvideo[height<={h}]/best[height<={h}]"
                    )
                else:
                    quality_format_map[h] = (
                        f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
                        f"/best[height<={h}][ext=mp4]/best[height<={h}]"
                    )
            if self.video_only:
                quality_format_map["best"] = "bestvideo[ext=mp4]/bestvideo/best"
            format_selector = quality_format_map.get(quality, quality_format_map["best"])
            
            # 视频格式：按画质选择，优先 mp4
            args.extend([
                "-f", format_selector,
                "--merge-output-format", str(self.merge_output_format)
            ])
        
        # 元数据：写出 .info.json（含发布时间 / 分P 标题 / 简介）。
        # `--no-clean-info-json` 是让 yt-dlp 保留完整字段，不要精简掉。
        if self.write_info_json:
            args.extend(["--write-info-json", "--no-clean-info-json"])
        
        # 弹幕（默认关；它是观众发言，定位是辅助信息而非训练语料）
        if self.download_danmaku:
            args.extend(["--write-subs", "--sub-langs", "danmaku"])
        
        # 反爬策略
        args.extend([
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "--referer", "https://www.bilibili.com",
            "--sleep-interval", str(self.sleep_interval),
            "--max-sleep-interval", str(self.max_sleep_interval),
            "--retries", str(self.retries),
            "--fragment-retries", str(self.fragment_retries),
            "--retry-sleep", str(self.retry_sleep),
            # 让进度按行输出。不加这个的话 yt-dlp 用 \r 原地刷新，
            # 被管道接住时会攒成一大块、最后才一起冒出来 ——
            # 表现就是「下载全程没有百分比，然后突然结束」。
            "--newline",
        ])

        # 断点续传：大文件下到一半中断后，重跑从断点继续而不是从头下。
        # yt-dlp 原生支持（-c），之前只是没传 —— 一个 2GB 视频下到 1.5GB
        # 挂掉就要重下，代价很大。
        if self.resume:
            args.append("--continue")
        
        # 仅在显式关闭校验时才跳过证书验证
        if not self.verify_ssl:
            args.append("--no-check-certificate")
        
        # 添加 URL
        args.append(video_url)
        
        return args

    def _stream_ytdlp_output(self, process) -> None:
        """逐行转发 yt-dlp 的输出，并把进度整理成"原地刷新"的一行。

        配合命令里的 `--newline`：yt-dlp 的进度行会按行给出，这里再用 `\\r`
        覆盖同一行，既能看到实时百分比，又不会把终端刷满。

        进度行形如：
            [download]  45.2% of  123.45MiB at  1.23MiB/s ETA 00:45
        其它行（Destination / 合并 / 错误等）正常换行输出。
        """
        in_progress = False
        for line in process.stdout:
            text = line.rstrip("\r\n")
            if not text:
                continue

            is_progress = text.startswith("[download]") and "%" in text
            if is_progress:
                print(f"\r  {text}", end="", flush=True)
                in_progress = True
            else:
                if in_progress:      # 结束上一行的原地刷新，避免与后续输出黏连
                    print()
                    in_progress = False
                print(f"  {text}")

            # 保留回调能力：调用方设了 progress_callback 就能拿到原始行
            if self.progress_callback:
                self.progress_callback(line)

        if in_progress:
            print()

    def _record_failure(self, url: str, reason: str, title: str = "") -> None:
        """记一条失败到清单（未配置清单时是空操作）。

        记录的意义：重跑时能用 `--retry-failed` 精确补齐，
        而不是只能靠「文件在不在」猜。
        """
        if self.failures is None:
            return
        rec = self.failures.record(url, reason=reason, title=title)
        print(f"  → 已记入失败清单（第 {rec.attempts} 次，类型：{rec.kind}）")

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
            # 之前失败过、现在文件在了 → 从失败清单移除，否则清单会越来越脏
            if self.failures is not None:
                self.failures.clear(video_url)
        else:
            print(f"正在下载：{safe_title}")
            
            # 构建命令
            args = self._build_download_args(video_url, output_path, 
                                           quality=quality,
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
                self._stream_ytdlp_output(process)
                
                process.wait()
                
                if process.returncode != 0:
                    print(f"✗ 下载失败，返回码：{process.returncode}")
                    self._record_failure(video_url, f"返回码 {process.returncode}",
                                         safe_title)
                    return None
                    
            except Exception as e:
                print(f"✗ 下载异常：{e}")
                self._record_failure(video_url, f"{type(e).__name__}: {e}", safe_title)
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
            
            self._stream_ytdlp_output(process)
            
            process.wait()
            
            if process.returncode == 0:
                print(f"✓ 音频下载完成：{output_path}")
                return output_path
            else:
                print("✗ 下载失败")
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
            "--newline",   # 进度按行输出，理由同 _build_download_args
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
            
            self._stream_ytdlp_output(process)
            
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
        try:
            from .channel_crawler import ChannelDownloader
        except ImportError:  # 直接以脚本方式运行本文件时回退
            from channel_crawler import ChannelDownloader
        downloader = ChannelDownloader(output_dir or self.output_dir, self.cookie_path, self.verify_ssl)
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
        # 保护 download_batch 中共享的结果列表
        self._lock = threading.Lock()
    
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
        
        def download_one(url):
            """单个下载任务，结果由线程池调度"""
            if audio_only:
                result = self.download_audio(url)
            else:
                result = self.download_video(url, download_subtitle=download_subtitle)
            
            if result:
                # 多线程共享列表，写入时加锁
                with self._lock:
                    downloaded.append(result)
            return result
        
        # 使用线程池控制并发，as_completed 等待全部完成（替代忙等）
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            futures = [executor.submit(download_one, url) for url in video_urls]
            for future in concurrent.futures.as_completed(futures):
                # 取出异常，避免被静默吞掉
                future.result()
        
        return downloaded
