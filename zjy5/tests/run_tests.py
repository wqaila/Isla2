#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""zjy5 采集工具的测试入口（全部离线，不需要网络 / GPU）。

    cd zjy5
    ./.venv/Scripts/python.exe tests/run_tests.py

各脚本独立（不是 pytest），任何一个失败都会让退出码非 0。

⚠️ 除 `test_cli_exit_code.py` 会起子进程跑 `--version` / `--help`
（以及一次 `--check-cookie`，它在无 cookie 文件时**不联网**）之外，
其余全部是纯内存测试。
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

TEST_MODULES = [
    "test_text_clean.py",
    "test_merge_and_srt.py",
    "test_harvest.py",
    "test_term_fix.py",
    "test_cli_defaults.py",
    "test_wbi.py",
    "test_channel_crawler.py",
    "test_cookie_manager.py",
    "test_cli_exit_code.py",
    "test_downloader_args.py",
    "test_download_failures.py",
    "test_retry_downloads.py",
    "test_downloader_extras.py",
    "test_download_result.py",
    "test_ffmpeg_locate.py",
    "test_cookie_flow.py",
    "test_param_validation.py",
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
