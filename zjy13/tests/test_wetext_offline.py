"""wetext 文本前端离线化补丁的测试。

背景（2026-11-09 排查）：
CosyVoice 的 ``frontend.py`` 用 try/except 依次尝试 ttsfrd → wetext → 无前端：

```python
try:
    from wetext import Normalizer as ZhNormalizer
    self.zh_tn_model = ZhNormalizer(remove_erhua=False)
    self.en_tn_model = EnNormalizer()
    self.text_frontend = 'wetext'
except:
    self.text_frontend = ''          # ← 静默降级，日志只写 "no frontend is avaliable"
```

wetext 的 ``Normalizer()`` 在**不传路径**时必定调用
``snapshot_download("pengzhendong/wetext")``，需要 ModelScope 鉴权。
本机无 token → ``HTTPError: Authentication token does not exist``
→ 被上面那个 bare ``except`` 吞掉 → **文本前端失效**。

后果：
- 服务能正常启动（所以很容易被忽略）
- 中文数字不被正则化："128" 送进 TTS 会读成 "一二八" 而不是 "一百二十八"
- 启动时表现为「卡在 Downloading Model ... 很久」，实际是在等鉴权超时

修复：在 ``load_model()`` 里、``import cosyvoice`` **之前**调用
``_patch_wetext_offline()``，给 ``wetext.Normalizer`` 套一层，
把缺省的路径参数替换成本地 fst，并同步修正 ``lang``。

本文件锁住这个补丁的关键约束。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TTS_SERVER = PROJECT_ROOT / "scripts" / "tts_server_wsl.py"


def _src() -> str:
    return TTS_SERVER.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# 补丁存在且被正确调用
# ----------------------------------------------------------------------


def test_patch_function_exists() -> None:
    assert "_patch_wetext_offline" in _src(), "缺少 _patch_wetext_offline()"


def test_patch_called_before_cosyvoice_import() -> None:
    """补丁必须在 import cosyvoice 之前执行。

    ``frontend.py`` 在 Frontend 实例化时就会构造 Normalizer，
    而 CosyVoice3(...) 的构造会实例化 Frontend。补丁晚于 import 就可能失效。
    """
    src = _src()
    patch_pos = src.index("_patch_wetext_offline()")
    import_pos = src.index("from cosyvoice.cli.cosyvoice import CosyVoice3")
    assert patch_pos < import_pos, (
        "补丁必须在 import cosyvoice 之前调用"
    )


def test_patch_uses_local_model_dir() -> None:
    src = _src()
    assert "pengzhendong" in src and "wetext" in src, (
        "补丁没有指向本地 wetext 模型目录"
    )


def test_patch_replaces_normalizer() -> None:
    """必须真正替换 wetext.Normalizer，否则调用方拿到的还是联网版。"""
    src = _src()
    assert "wetext.Normalizer =" in src


# ----------------------------------------------------------------------
# 关键约束：传路径时 lang 必须是 en/zh
# ----------------------------------------------------------------------


def test_patch_sets_lang_when_paths_given() -> None:
    """传了 tagger_path/verbalizer_path 时，lang 必须被改成 en 或 zh。

    wetext 源码里有硬断言：
        assert lang in ("en", "zh"), "Language must be 'en' or 'zh' when using custom tagger and verbalizer."
    而 CosyVoice 调用时用的是默认 lang="auto"，不修正会直接 AssertionError。
    """
    src = _src()
    assert 'kwargs["lang"] = lang' in src, (
        "补丁没有修正 lang，会触发 wetext 的 AssertionError"
    )


def test_patch_distinguishes_zh_and_en() -> None:
    """要能区分中文与英文两次调用。

    CosyVoice 的两次调用都不带 lang：
        ZhNormalizer(remove_erhua=False)   # 中文
        EnNormalizer()                      # 英文
    """
    src = _src()
    assert "remove_erhua" in src, "没有用 remove_erhua 作为中文线索"
    assert "enable_0_to_9" in src, "没有用 enable_0_to_9 作为中文线索"


def test_patch_sets_both_paths() -> None:
    src = _src()
    assert 'kwargs["tagger_path"]' in src
    assert 'kwargs["verbalizer_path"]' in src


# ----------------------------------------------------------------------
# 失败要可见，不能静默
# ----------------------------------------------------------------------


def test_patch_warns_when_model_missing() -> None:
    """本地模型不完整时要 warn，而不是静默回到联网。"""
    src = _src()
    # 找补丁函数体里的 warning
    start = src.index("def _patch_wetext_offline")
    end = src.index("def load_model")
    body = src[start:end]
    assert "logger.warning" in body, "模型缺失时没有告警"


def test_patch_logs_success() -> None:
    """成功切换要留日志，便于确认「use wetext frontend」而不是空前端。"""
    src = _src()
    start = src.index("def _patch_wetext_offline")
    end = src.index("def load_model")
    body = src[start:end]
    assert "logger.info" in body


# ----------------------------------------------------------------------
# 文档记录
# ----------------------------------------------------------------------


def test_documented_in_worklog() -> None:
    """这个坑必须写进文档，否则下次换机器会重踩。"""
    worklog = PROJECT_ROOT / "docs" / "WORKLOG-2026-11-09.md"
    assert worklog.is_file()
    text = worklog.read_text(encoding="utf-8")
    assert "wetext" in text
    assert "Authentication token" in text or "鉴权" in text
