#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
搜索视频爬取模块
支持搜索视频并下载
"""

import os
import re
import json
import time
import urllib.request
import urllib.parse
import ssl
from typing import Optional, List, Dict, Any
from datetime import datetime


class SearchCrawler:
    """Bilibili 搜索爬取器"""
    
    def __init__(self, cookie_path: str = None):
        """
        :param cookie_path: Cookie 文件路径
        """
        self.cookie_path = cookie_path
        self.cookies = self._load_cookies()
        
        # SSL 上下文
        self.ssl_context = ssl.create_default_context()
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
    
    def search_videos(self, keyword: str, page: int = 1, page_size: int = 20,
                      order: str = "totalrank") -> List[Dict]:
        """
        搜索视频
        :param keyword: 搜索关键词
        :param page: 页码
        :param page_size: 每页数量
        :param order: 排序方式 (totalrank/click/pubdate/stow)
        :return: 视频列表
        """
        # 排序映射
        order_map = {
            "totalrank": 0,    # 综合排序
            "click": 1,        # 最多点击
            "pubdate": 2,      # 最新发布
            "stow": 7,         # 最多收藏
        }
        
        order_value = order_map.get(order, 0)
        
        # 搜索 API
        api_url = "https://api.bilibili.com/x/web-interface/search/type"
        
        # 编码关键词
        encoded_keyword = urllib.parse.quote(keyword)
        
        # 构建参数
        params = {
            "keyword": encoded_keyword,
            "page": page,
            "page_size": page_size,
            "order": order_value,
            "platform": "web",
            "web_location": "87153",
        }
        
        # 构建请求
        query = urllib.parse.urlencode(params)
        full_url = f"{api_url}?{query}"
        
        print(f"  搜索：{full_url}")
        
        req = urllib.request.Request(
            full_url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': f'https://search.bilibili.com/all?keyword={encoded_keyword}',
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
                    items = result.get('result', [])
                    
                    videos = []
                    for item in items:
                        if item.get('type') == 'video':
                            videos.append({
                                'video_id': item.get('bvid', item.get('aid', '')),
                                'title': item.get('title', ''),
                                'author': item.get('author', ''),
                                'duration': item.get('duration', '0:00'),
                                'view_count': item.get('play', 0),
                                'publish_time': item.get('pubdate', 0),
                                'url': f"https://www.bilibili.com/video/{item.get('bvid', '')}",
                            })
                    
                    return videos
                else:
                    print(f"  API 返回错误：{data.get('message', '未知错误')}")
                    return []
                    
        except Exception as e:
            print(f"  搜索失败：{e}")
            return []


class SearchDownloader:
    """搜索视频下载器"""
    
    def __init__(self, output_dir: str = "downloads", cookie_path: str = None):
        """
        :param output_dir: 输出目录
        :param cookie_path: Cookie 路径
        """
        self.output_dir = output_dir
        self.cookie_path = cookie_path
        self.crawler = SearchCrawler(cookie_path)
    
    def download_search_results(self, keyword: str, max_videos: int = 10,
                                 order: str = "totalrank", audio_only: bool = False) -> List[str]:
        """
        下载搜索结果
        :param keyword: 搜索关键词
        :param max_videos: 最大下载数量
        :param order: 排序方式
        :param audio_only: 是否只下载音频
        :return: 下载的文件路径列表
        """
        # 搜索视频
        print(f"正在搜索：{keyword}")
        videos = self.crawler.search_videos(keyword, order=order)
        
        if not videos:
            print("未找到视频")
            return []
        
        print(f"找到 {len(videos)} 个视频")
        
        # 下载视频
        from downloader import BilibiliDownloader
        downloader = BilibiliDownloader(self.output_dir, self.cookie_path)
        
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
