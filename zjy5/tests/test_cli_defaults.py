#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""CLI 默认值与库常量一致性的测试（纯标准库，不导入 main.py 的重依赖）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_cli_defaults.py

【为什么需要这个测试】
`main.py` 依赖 cv2 / paddle / whisper，**导入它就会拉起一堆重依赖**，
所以不能直接 import 来断言它的 argparse 默认值。

但「库改了、CLI 没改」是**真实发生过**的缺陷（2026-08-xx 的
`--speaker-model`：`speaker_filter.DEFAULT_MODEL` 已改成
`qwen3.5:9b-gguf`，而 `main.py` 的 CLI 默认值仍是 `qwen3:8b`）。
失败模式是**静默的** —— 不带参数跑就打标失败，而打标失败的设计是
「不丢数据、只跳过打分」，用户只会发现结果里没有 `speaker_score`。

所以这里改用**源码级断言**：直接读 `main.py` 的文本，
把 `add_argument` 的默认值抠出来与库常量比对。
不 import、不执行，因此完全离线、零依赖。
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "chibtaici"))

from speaker_filter import DEFAULT_MODEL, DEFAULT_ROLE  # noqa: E402

MAIN_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, "chibtaici", "main.py")


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


def _read_main():
    with open(MAIN_PY, encoding="utf-8") as f:
        return f.read()


def _arg_default(src, flag):
    """抠出 `add_argument("<flag>", default=<值>` 里的默认值。

    用正则而非 argparse：argparse 需要真正执行 main() 才能拿到默认值，
    而 main() 会拉起 cv2/paddle/whisper。
    """
    # 允许 add_argument 与 default 跨行
    pat = re.compile(
        r'add_argument\(\s*["\']' + re.escape(flag) + r'["\']'
        r'(?:(?!add_argument).)*?default\s*=\s*([^,\)]+)',
        re.S)
    m = pat.search(src)
    if not m:
        return None
    return m.group(1).strip()


def test_speaker_model_default_matches_library():
    """`--speaker-model` 的 CLI 默认值必须与 `speaker_filter.DEFAULT_MODEL` 一致。

    ⚠️ 这是回归测试：曾经两者不一致（CLI=qwen3:8b、库=qwen3.5:9b-gguf），
    而本机**从未安装过 qwen3:8b**，导致「静默跳过打分」。
    """
    src = _read_main()
    cli = _arg_default(src, "--speaker-model")
    ok(cli is not None, "没找到 --speaker-model 的 default")
    # CLI 应当引用常量本身，而不是再抄一份字面量
    ok("DEFAULT_MODEL" in cli,
       f"--speaker-model 的默认值应引用 DEFAULT_MODEL，实际是 {cli!r}")
    eq(DEFAULT_MODEL, "qwen3.5:9b-gguf", "库默认值应指向本机已安装的模型")


def test_speaker_role_default_matches_library():
    """`--speaker-role` 同理，避免角色名再次分叉。"""
    src = _read_main()
    cli = _arg_default(src, "--speaker-role")
    ok(cli is not None, "没找到 --speaker-role 的 default")
    ok("DEFAULT_ROLE" in cli,
       f"--speaker-role 的默认值应引用 DEFAULT_ROLE，实际是 {cli!r}")
    eq(DEFAULT_ROLE, "爱莉希雅", "库默认角色名")


def test_main_imports_constants():
    """main.py 必须真的 import 这两个常量，否则引用会 NameError。"""
    src = _read_main()
    ok(re.search(r'from speaker_filter import[^\n]*DEFAULT_MODEL', src),
       "main.py 应 import DEFAULT_MODEL")
    ok(re.search(r'from speaker_filter import[^\n]*DEFAULT_ROLE', src),
       "main.py 应 import DEFAULT_ROLE")


def test_help_text_not_stale():
    """help 文案里不该再出现旧模型名（会误导用户以为默认是它）。"""
    src = _read_main()
    seg = src[src.find('"--speaker-model"'):src.find('"--speaker-model"') + 300]
    ok("qwen3:8b" not in seg,
       "help 文案仍写着 qwen3:8b —— 该模型本机从未安装，会误导")


TESTS = [
    test_speaker_model_default_matches_library,
    test_speaker_role_default_matches_library,
    test_main_imports_constants,
    test_help_text_not_stale,
]


def run():
    passed = 0
    failed = 0
    for test in TESTS:
        try:
            test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except Exception as e:                      # 打印出来，别让失败悄悄过去
            print(f"  ✗ {test.__name__}：{type(e).__name__}: {e}")
            failed += 1
    print(f"\ntest_cli_defaults：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
