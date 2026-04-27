#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI 字幕提取模块
支持通过 API 和 Playwright 两种方式提取 Bilibili AI 字幕
"""

import os
import re
import json
import time
import urllib.request
import urllib.parse
import ssl
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime


class SubtitleExtractor:
    """Bilibili AI 字幕提取器"""
    
    def __init__(self, cookie_path: str = None, output_dir: str = "downloads"):
        """
        :param cookie_path: Cookie 文件路径
        :param output_dir: 输出目录
        """
        self.cookie_path = cookie_path
        self.output_dir = output_dir
        self.cookies = self._load_cookies()
        
        # SSL 上下文（跳过证书验证）
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
    
    def _load_cookies(self) -> Dict[str, str]:
        """从 Cookie 文件加载 Cookie"""
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
    
    def _get_video_id(self, url: str) -> Optional[str]:
        """从 URL 中提取视频 ID"""
        patterns = [
            r'video/(BV\w+)',
            r'BV(\w+)',
            r'av(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return None
    
    def _get_subtitle_url_via_api(self, video_url: str) -> Optional[str]:
        """通过 API 获取字幕 URL"""
        try:
            video_id = self._get_video_id(video_url)
            if not video_id:
                print("  无法从 URL 提取视频 ID")
                return None
            
            # Bilibili 字幕 API
            api_url = f"https://api.bilibili.com/x/player/wbi/v2?cid=1&aid=0&bvid={video_id}"
            
            print(f"  请求 API: {api_url}")
            
            req = urllib.request.Request(
                api_url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': 'https://www.bilibili.com',
                }
            )
            
            # 添加 Cookie
            if self.cookies:
                cookie_str = '; '.join(f"{k}={v}" for k, v in self.cookies.items())
                req.add_header('Cookie', cookie_str)
            
            with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                if data.get('code') == 0:
                    result = data.get('data', {})
                    subtitle = result.get('subtitle', {})
                    subtitles = subtitle.get('list', [])
                    
                    if subtitles:
                        # 获取第一个字幕 URL
                        first_subtitle = subtitles[0]
                        subtitle_url = first_subtitle.get('subtitle_url', '')
                        if subtitle_url:
                            print(f"  找到字幕：{subtitle_url}")
                            return subtitle_url
                
                print(f"  API 返回：{data.get('message', '未知错误')}")
                return None
                
        except Exception as e:
            print(f"  API 请求失败：{e}")
            return None
    
    def _download_subtitle_file(self, subtitle_url: str, output_path: str) -> bool:
        """下载字幕文件"""
        try:
            print(f"  下载字幕：{subtitle_url}")
            
            req = urllib.request.Request(
                subtitle_url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                }
            )
            
            with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as response:
                content = response.read().decode('utf-8')
                
                # 解析字幕内容
                subtitle_data = json.loads(content)
                lines = subtitle_data.get('body', [])
                
                # 提取纯文本
                text_lines = []
                for line in lines:
                    text = line.get('content', '')
                    if text:
                        text_lines.append(text)
                
                # 写入文件
                os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(text_lines))
                
                print(f"  字幕已保存：{output_path}")
                return True
                
        except Exception as e:
            print(f"  下载字幕失败：{e}")
            return False
    
    def _extract_subtitle_playwright(self, video_url: str, safe_title: str, 
                                      output_dir: str) -> Optional[str]:
        """使用 Playwright 提取 AI 字幕"""
        try:
            from playwright.sync_api import sync_playwright
            
            print("  启动浏览器...")
            
            with sync_playwright() as p:
                # 启动浏览器
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )
                page = context.new_page()
                
                # 添加 Cookie
                if self.cookies:
                    cookie_list = []
                    for name, value in self.cookies.items():
                        cookie_list.append({
                            'name': name,
                            'value': value,
                            'domain': '.bilibili.com',
                            'path': '/'
                        })
                    context.add_cookies(cookie_list)
                
                print(f"  访问页面：{video_url}")
                page.goto(video_url, wait_until='networkidle', timeout=60000)
                
                # 等待页面加载
                time.sleep(3)
                
                # 尝试通过 API 获取字幕
                subtitle_url = None
                
                # 方法 1: 从页面数据中获取
                try:
                    result = page.evaluate('''() => {
                        const jsonText = document.querySelector('#__INITIAL_STATE__')?.textContent;
                        if (jsonText) {
                            const data = JSON.parse(jsonText);
                            const subtitle = data?.videoData?.subtitle;
                            if (subtitle?.list && subtitle.list.length > 0) {
                                return subtitle.list[0].subtitle_url;
                            }
                        }
                        return null;
                    }''')
                    if result:
                        subtitle_url = result
                        print(f"  从页面数据获取字幕：{subtitle_url}")
                except Exception as e:
                    print(f"  从页面数据获取失败：{e}")
                
                # 方法 2: 监听网络请求
                if not subtitle_url:
                    print("  监听字幕 API 请求...")
                    subtitle_responses = []
                    
                    def handle_response(response):
                        try:
                            url = response.url
                            if 'subtitle' in url or 'langsub' in url:
                                subtitle_responses.append(url)
                        except:
                            pass
                    
                    page.on('response', handle_response)
                    
                    # 重新加载页面以捕获请求
                    page.reload(wait_until='networkidle', timeout=60000)
                    time.sleep(2)
                    
                    if subtitle_responses:
                        subtitle_url = subtitle_responses[0]
                        print(f"  捕获字幕 URL: {subtitle_url}")
                    
                    page.off('response', handle_response)
                
                # 方法 3: 直接调用 API
                if not subtitle_url:
                    try:
                        video_id = self._get_video_id(video_url)
                        if video_id:
                            # 获取视频详情
                            detail_url = f"https://api.bilibili.com/x/web-interface/view?bvid={video_id}"
                            resp = page.evaluate(f'''() => fetch("{detail_url}", {{
                                headers: {{
                                    'User-Agent': 'Mozilla/5.0',
                                    'Referer': '{video_url}'
                                }}
                            }}).then(r => r.json())''')
                            
                            if resp and resp.get('code') == 0:
                                cid = resp.get('data', {}).get('cid')
                                if cid:
                                    # 获取字幕
                                    sub_url = f"https://api.bilibili.com/x/player/wbi/v2?cid={cid}&bvid={video_id}"
                                    sub_resp = page.evaluate(f'''() => fetch("{sub_url}", {{
                                        headers: {{
                                            'User-Agent': 'Mozilla/5.0',
                                            'Referer': '{video_url}'
                                        }}
                                    }}).then(r => r.json())''')
                                    
                                    if sub_resp and sub_resp.get('code') == 0:
                                        subtitle_data = sub_resp.get('data', {}).get('subtitle', {})
                                        subtitles = subtitle_data.get('list', [])
                                        if subtitles:
                                            subtitle_url = subtitles[0].get('subtitle_url')
                                            print(f"  通过 API 获取字幕：{subtitle_url}")
                    except Exception as e:
                        print(f"  API 调用失败：{e}")
                
                if subtitle_url:
                    # 下载字幕
                    os.makedirs(output_dir, exist_ok=True)
                    output_path = os.path.join(output_dir, f"{safe_title}.txt")
                    
                    try:
                        resp = page.evaluate(f'''() => fetch("{subtitle_url}").then(r => r.json())''')
                        if resp and 'body' in resp:
                            lines = resp.get('body', [])
                            text_lines = [line.get('content', '') for line in lines if line.get('content')]
                            
                            with open(output_path, 'w', encoding='utf-8') as f:
                                f.write('\n'.join(text_lines))
                            
                            print(f"  字幕已保存：{output_path}")
                            browser.close()
                            return output_path
                    except Exception as e:
                        print(f"  下载字幕失败：{e}")
                
                browser.close()
                print("  未找到字幕")
                return None
                
        except Exception as e:
            print(f"  Playwright 提取失败：{e}")
            import traceback
            traceback.print_exc()
            return None
    
    def extract_subtitle(self, video_url: str, output_dir: str = None,
                         output_format: str = "text", use_api_first: bool = True) -> Optional[str]:
        """
        提取字幕
        :param video_url: 视频 URL
        :param output_dir: 输出目录
        :param output_format: 输出格式 (text/srt)
        :param use_api_first: 是否优先使用 API
        :return: 字幕文件路径
        """
        output_dir = output_dir or self.output_dir
        video_id = self._get_video_id(video_url)
        safe_title = video_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        
        os.makedirs(output_dir, exist_ok=True)
        
        # 优先使用 API
        if use_api_first:
            subtitle_url = self._get_subtitle_url_via_api(video_url)
            if subtitle_url:
                output_path = os.path.join(output_dir, f"{safe_title}.txt")
                if self._download_subtitle_file(subtitle_url, output_path):
                    return output_path
        
        # 使用 Playwright
        print("  使用 Playwright 提取字幕...")
        return self._extract_subtitle_playwright(video_url, safe_title, output_dir)


def extract_bilibili_subtitle(video_url: str, output_path: str = None,
                               cookie_path: str = None) -> Optional[str]:
    """
    便捷函数：提取 Bilibili AI 字幕
    :param video_url: 视频 URL
    :param output_path: 输出路径
    :param cookie_path: Cookie 路径
    :return: 字幕文件路径
    """
    extractor = SubtitleExtractor(cookie_path=cookie_path)
    
    if output_path:
        output_dir = os.path.dirname(output_path)
        safe_title = os.path.basename(output_path).replace('.txt', '').replace('.srt', '')
    else:
        output_dir = "downloads"
        safe_title = None
    
    if safe_title:
        return extractor._extract_subtitle_playwright(video_url, safe_title, output_dir)
    else:
        video_id = extractor._get_video_id(video_url)
        safe_title = video_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        return extractor._extract_subtitle_playwright(video_url, safe_title, output_dir)
