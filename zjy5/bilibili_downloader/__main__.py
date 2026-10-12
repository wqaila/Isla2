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
        # ⚠️ 必须把返回值传给 sys.exit：`main_cli()` 里有多处 `return 1`，
        #    裸调用会把退出码丢掉，导致**所有失败路径都返回 0** ——
        #    脚本在 CI / shell 里就无法用 `$?` 判断成败。
        #    `or 0` 兜住没有显式 return 的路径（返回 None）。
        sys.exit(main_cli() or 0)
    else:
        main()
