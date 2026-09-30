#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
频道视频爬取模块
支持获取频道最新/最热视频列表
"""

import os
import re
import json
import time
import urllib.request
import urllib.parse
import ssl
from typing import Optional, List, Dict

try:
    from .wbi import build_signed_query
except ImportError:  # 直接以脚本方式运行本文件时回退
    from wbi import build_signed_query


class ChannelCrawler:
    """Bilibili 频道爬取器"""
    
    def __init__(self, cookie_path: str = None, verify_ssl: bool = True):
        """
        :param cookie_path: Cookie 文件路径
        :param verify_ssl: 是否校验 HTTPS 证书（默认校验，仅在明确要求时关闭）
        """
        self.cookie_path = cookie_path
        self.cookies = self._load_cookies()
        
        # SSL 上下文（默认校验证书）
        self.ssl_context = ssl.create_default_context()
        if not verify_ssl:
            # 仅在显式关闭时才跳过证书验证
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE
    
    def _load_cookies(self) -> Dict[str, str]:
        """加载 Cookie"""
        cookies = {}
        if self.cookie_path and os.path.exists(self.cookie_path):
            try:
                with open(self.cookie_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            parts = line.split('\t')
                            if len(parts) >= 7:
                                cookies[parts[5]] = parts[6]
            except Exception as e:
                print(f"加载 Cookie 失败：{e}")
        return cookies
    
    def _get_channel_id(self, url: str) -> Optional[str]:
        """从 URL 中提取频道 ID"""
        patterns = [
            r'space\.bilibili\.com/(\d+)',
            r'mid=(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return None
    
    def get_channel_videos(self, channel_id: str, page: int = 1, 
                           page_size: int = 30, order: str = "pubdate") -> List[Dict]:
        """
        获取频道视频列表
        :param channel_id: 频道 ID
        :param page: 页码
        :param page_size: 每页数量
        :param order: 排序方式 (pubdate/latest/hot)
        :return: 视频列表
        """
        # 排序映射
        order_map = {
            "pubdate": 1,    # 最新发布
            "latest": 1,     # 最新发布
            "hot": 3,        # 最热
            "view": 2,       # 最多播放
        }
        
        order_value = order_map.get(order, 1)
        
        # 频道视频 API
        api_url = "https://api.bilibili.com/x/space/wbi/arc/search"
        
        # 构建参数
        params = {
            "mid": channel_id,
            "pn": page,
            "ps": page_size,
            "order": order_value,
            "platform": "web",
            "web_location": "333.934",
        }
        
        # 该接口需要 WBI 签名（w_rid/wts），否则返回 -352
        try:
            query = build_signed_query(
                params,
                ssl_context=self.ssl_context,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': f'https://space.bilibili.com/{channel_id}/video',
                }
            )
        except Exception as e:
            print(f"  WBI 签名失败，无法请求频道视频接口：{e}")
            return []
        
        # 构建请求
        full_url = f"{api_url}?{query}"
        
        print(f"  请求频道视频：{full_url}")
        
        req = urllib.request.Request(
            full_url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': f'https://space.bilibili.com/{channel_id}/video',
            }
        )
        
        # 添加 Cookie
        if self.cookies:
            cookie_str = '; '.join(f"{k}={v}" for k, v in self.cookies.items())
            req.add_header('Cookie', cookie_str)
        
        try:
            with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                if data.get('code') == 0:
                    result = data.get('data', {})
                    vlist = result.get('vlist', [])
                    
                    videos = []
                    for video in vlist:
                        videos.append({
                            'video_id': video.get('bvid', video.get('aid', '')),
                            'title': video.get('title', ''),
                            'author': video.get('author', ''),
                            'duration': video.get('length', '0:00'),
                            'view_count': video.get('play', 0),
                            'publish_time': video.get('created', 0),
                            'url': f"https://www.bilibili.com/video/{video.get('bvid', '')}",
                        })
                    
                    return videos
                else:
                    print(f"  API 返回错误：{data.get('message', '未知错误')}")
                    return []
                    
        except Exception as e:
            print(f"  请求失败：{e}")
            return []
    
    def get_all_channel_videos(self, channel_id: str, max_count: int = 0,
                               order: str = "pubdate", page_size: int = 30,
                               page_delay: float = 0.8,
                               progress_cb=None) -> List[Dict]:
        """翻页拉取 UP 主空间**全部**（或前 max_count 个）视频。

        `get_channel_videos()` 只取一页（默认 30 条）——批量收割字幕时不够用，
        一个合集动辄几十上百个视频，所以这里把翻页逻辑补上。

        :param max_count: 最多取多少个，0 表示不限（一直翻到空页）
        :param page_delay: 每页之间的间隔秒数，避免请求过密
        :param progress_cb: 可选回调 (page, got_total, cumulative_list)
        """
        videos: List[Dict] = []
        page = 1
        # 保护性上限：正常 UP 主不会有这么多页，出现只可能是接口行为变了
        max_pages = 200

        while page <= max_pages:
            batch = self.get_channel_videos(
                channel_id, page=page, page_size=page_size, order=order
            )
            if not batch:
                break

            videos.extend(batch)
            if progress_cb:
                progress_cb(page, len(batch), videos)

            if max_count and len(videos) >= max_count:
                videos = videos[:max_count]
                break

            # 不足一页说明已经到底了
            if len(batch) < page_size:
                break

            page += 1
            time.sleep(page_delay if page_delay and page_delay > 0 else 0)

        return videos

    def get_channel_info(self, channel_id: str) -> Optional[Dict]:
        """获取频道信息"""
        api_url = "https://api.bilibili.com/x/space/wbi/acc/info"
        
        # 该接口同样需要 WBI 签名
        try:
            query = build_signed_query(
                {"mid": channel_id},
                ssl_context=self.ssl_context,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                }
            )
        except Exception as e:
            print(f"获取频道信息失败：WBI 签名失败：{e}")
            return None
        
        req = urllib.request.Request(
            f"{api_url}?{query}",
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
        )
        
        if self.cookies:
            cookie_str = '; '.join(f"{k}={v}" for k, v in self.cookies.items())
            req.add_header('Cookie', cookie_str)
        
        try:
            with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                if data.get('code') == 0:
                    return data.get('data', {})
                return None
        except Exception as e:
            print(f"获取频道信息失败：{e}")
            return None


class ChannelDownloader:
    """频道视频下载器"""
    
    def __init__(self, output_dir: str = "downloads", cookie_path: str = None,
                 verify_ssl: bool = True):
        """
        :param output_dir: 输出目录
        :param cookie_path: Cookie 路径
        :param verify_ssl: 是否校验 HTTPS 证书（默认校验）
        """
        self.output_dir = output_dir
        self.cookie_path = cookie_path
        self.verify_ssl = verify_ssl
        self.crawler = ChannelCrawler(cookie_path, verify_ssl=verify_ssl)
    
    def download_by_url(self, channel_url: str, max_videos: int = 10,
                        audio_only: bool = False, order: str = "pubdate") -> List[str]:
        """
        下载频道视频
        :param channel_url: 频道 URL
        :param max_videos: 最大下载数量
        :param audio_only: 是否只下载音频
        :param order: 排序方式
        :return: 下载的文件路径列表
        """
        channel_id = self.crawler._get_channel_id(channel_url)
        if not channel_id:
            print("无法从 URL 提取频道 ID")
            return []
        
        # 获取频道信息
        channel_info = self.crawler.get_channel_info(channel_id)
        channel_name = channel_info.get('name', '未知频道') if channel_info else '未知频道'
        print(f"频道：{channel_name}")
        
        # 获取视频列表
        print("正在获取最新视频...")
        videos = self.crawler.get_channel_videos(channel_id, order=order)
        
        if not videos:
            print("未获取到视频列表")
            return []
        
        print(f"获取到 {len(videos)} 个视频")
        
        # 下载视频
        try:
            from .downloader import BilibiliDownloader
        except ImportError:  # 直接以脚本方式运行本文件时回退
            from downloader import BilibiliDownloader
        downloader = BilibiliDownloader(self.output_dir, self.cookie_path, self.verify_ssl)
        
        downloaded = []
        for i, video in enumerate(videos[:max_videos]):
            print(f"\n[{i+1}/{min(max_videos, len(videos))}] {video['title']}")
            
            if audio_only:
                result = downloader.download_audio(video['url'], video['title'])
            else:
                result = downloader.download_video(video['url'], video['title'])
            
            if result:
                downloaded.append(result)
            
            # 请求间隔
            time.sleep(2)
        
        return downloaded
