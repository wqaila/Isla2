#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bilibili WBI 签名工具
B 站部分 web 接口（如 x/space/wbi/arc/search、x/web-interface/search/type）
要求携带 w_rid/wts 签名，否则返回 -352。

签名算法（B 站公开算法）：
1. 从 nav 接口取 img_key / sub_key；
2. 按固定的 mixinKeyEncTab 重排 img_key + sub_key，取前 32 位作为 mixin key；
3. 参数按 key 排序拼接为 query（过滤 value 中的 !'()* 字符）后加上 wts，
   再对 query + mixin key 做 md5 得到 w_rid。
"""

import time
import json
import hashlib
import urllib.parse
import urllib.request
from typing import Dict, Optional, Tuple

# B 站固定的 mixinKeyEncTab 重排表
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]

# nav 接口返回的密钥有效期，缓存 1 小时
_KEY_CACHE_TTL = 3600

# 模块级缓存：(mixin_key, 过期时间戳)
_mixin_key_cache: Optional[Tuple[str, float]] = None


def get_mixin_key(orig: str) -> str:
    """按 mixinKeyEncTab 重排 img_key + sub_key，取前 32 位"""
    return ''.join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _fetch_wbi_keys(ssl_context=None, headers: Dict[str, str] = None) -> Tuple[str, str]:
    """从 nav 接口获取 img_key 与 sub_key"""
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.bilibili.com',
    }
    if headers:
        default_headers.update(headers)

    req = urllib.request.Request(
        "https://api.bilibili.com/x/web-interface/nav",
        headers=default_headers
    )
    with urllib.request.urlopen(req, timeout=30, context=ssl_context) as response:
        data = json.loads(response.read().decode('utf-8'))

    wbi_img = data.get('data', {}).get('wbi_img', {})
    img_url = wbi_img.get('img_url', '')
    sub_url = wbi_img.get('sub_url', '')

    # 从 URL 中截取文件名（去掉扩展名）作为 key
    img_key = img_url.rsplit('/', 1)[-1].split('.')[0]
    sub_key = sub_url.rsplit('/', 1)[-1].split('.')[0]

    if not img_key or not sub_key:
        raise RuntimeError("无法从 nav 接口获取 wbi 密钥，可能被风控拦截")

    return img_key, sub_key


def get_mixin_key_cached(ssl_context=None, headers: Dict[str, str] = None) -> str:
    """获取（带缓存的）mixin key"""
    global _mixin_key_cache

    now = time.time()
    if _mixin_key_cache and now < _mixin_key_cache[1]:
        return _mixin_key_cache[0]

    img_key, sub_key = _fetch_wbi_keys(ssl_context=ssl_context, headers=headers)
    mixin_key = get_mixin_key(img_key + sub_key)
    _mixin_key_cache = (mixin_key, now + _KEY_CACHE_TTL)
    return mixin_key


def sign_params(params: Dict, ssl_context=None, headers: Dict[str, str] = None) -> Dict:
    """
    对参数进行 WBI 签名
    :param params: 原始请求参数
    :param ssl_context: SSL 上下文（可选）
    :param headers: 获取密钥时使用的请求头（可选）
    :return: 追加了 wts / w_rid 的新参数字典
    """
    mixin_key = get_mixin_key_cached(ssl_context=ssl_context, headers=headers)

    signed = dict(params)
    signed['wts'] = int(time.time())

    # 参数按 key 排序，value 过滤掉 !'()* 字符
    filtered = {
        k: ''.join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in sorted(signed.items())
    }
    query = urllib.parse.urlencode(filtered)
    signed['w_rid'] = hashlib.md5((query + mixin_key).encode('utf-8')).hexdigest()

    return signed


def build_signed_query(params: Dict, ssl_context=None,
                       headers: Dict[str, str] = None) -> str:
    """签名并直接返回 URL query 字符串"""
    return urllib.parse.urlencode(sign_params(params, ssl_context=ssl_context, headers=headers))
