#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
模块入口
支持 python -m bilibili_downloader 运行
- 带命令行参数时走 CLI（main_cli）
- 不带参数时进入交互模式（main）
"""

import sys

try:
    from .main import main, main_cli
except ImportError:  # 直接以脚本方式运行本文件时回退
    from main import main, main_cli

if __name__ == "__main__":
    if len(sys.argv) > 1:
        main_cli()
    else:
        main()
