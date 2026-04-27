#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
运行脚本
方便直接运行下载器
"""

import sys
import os

# 添加当前目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 直接执行 main.py
if __name__ == "__main__":
    # 导入 main 模块中的函数
    import importlib.util
    spec = importlib.util.spec_from_file_location("main_module", os.path.join(os.path.dirname(__file__), "main.py"))
    main_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main_module)
    
    # 调用 main 函数
    if hasattr(main_module, 'main'):
        main_module.main()
    else:
        print("错误：main.py 中没有 main 函数")
