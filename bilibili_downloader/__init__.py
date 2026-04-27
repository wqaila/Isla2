#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bilibili 视频/音频下载器
支持频道视频爬取、搜索视频爬取、AI 字幕提取
"""

from .downloader import BilibiliDownloader, AsyncDownloader
from .channel_crawler import ChannelDownloader, ChannelCrawler
from .search_crawler import SearchDownloader, SearchCrawler
from .cookie_manager import CookieManager
from .subtitle_extractor import SubtitleExtractor, extract_bilibili_subtitle

__version__ = "1.0.0"
__all__ = [
    "BilibiliDownloader",
    "AsyncDownloader",
    "ChannelDownloader",
    "ChannelCrawler",
    "SearchDownloader",
    "SearchCrawler",
    "CookieManager",
    "SubtitleExtractor",
    "extract_bilibili_subtitle",
]
