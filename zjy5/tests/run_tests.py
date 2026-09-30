#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""zjy5 采集工具的测试入口（全部离线，不需要网络 / GPU）。

    cd zjy5
    ./.venv/Scripts/python.exe tests/run_tests.py

三个脚本各自独立（不是 pytest），任何一个失败都会让退出码非 0。
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

TEST_MODULES = [
    "test_text_clean.py",
    "test_merge_and_srt.py",
    "test_harvest.py",
]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    print("=" * 52)
    print("zjy5 采集工具测试")
    print("=" * 52)

    failed = []
    for filename in TEST_MODULES:
        print(f"\n--- {filename} ---")
        module = load(os.path.join(HERE, filename), filename.replace(".py", ""))
        if not module.run():
            failed.append(filename)

    print("\n" + "=" * 52)
    if failed:
        print(f"失败：{'、'.join(failed)}")
        return 1
    print(f"全部通过（{len(TEST_MODULES)} 个脚本）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
