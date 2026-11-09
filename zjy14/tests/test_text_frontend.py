"""文本前端补丁的测试。

重点：
1. 本地 fst 资源能被正确发现
2. TN 转换结果正确（数字 -> 中文读法）
3. 注入 frontend 后 text_frontend 字段正确
4. 资源缺失时给出可读错误，不静默降级
"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_voice.inference.text_frontend import (
    DEFAULT_WETEXT_CACHE,
    LocalWetextNormalizer,
    TextFrontendError,
    _find_wetext_root,
    enable_wetext_frontend,
    frontend_status,
)

#: 本地是否有可用的 wetext 资源（没有则跳过依赖它的测试）
HAS_RESOURCE = (DEFAULT_WETEXT_CACHE / "zh" / "tn" / "tagger.fst").is_file()

requires_resource = pytest.mark.skipif(
    not HAS_RESOURCE, reason="本机没有 wetext fst 资源"
)


def test_default_cache_path_shape() -> None:
    """默认路径应指向 modelscope 缓存目录。"""
    assert "modelscope" in str(DEFAULT_WETEXT_CACHE)
    assert "wetext" in str(DEFAULT_WETEXT_CACHE)


def test_missing_resource_raises_readable_error(tmp_path: Path, monkeypatch) -> None:
    """资源缺失时必须报可读错误，不能静默降级。

    注意：_find_wetext_root 会回退到默认缓存路径，
    这里通过 monkeypatch 把默认路径也指向不存在的目录，
    确保测的是「全都找不到」的分支。
    """
    import custom_voice.inference.text_frontend as tf

    monkeypatch.setattr(tf, "DEFAULT_WETEXT_CACHE", tmp_path / "no-default")
    with pytest.raises(TextFrontendError) as ei:
        _find_wetext_root(tmp_path / "nonexistent")
    msg = str(ei.value)
    assert "找不到" in msg
    # 错误信息里应列出查找过的路径
    assert "nonexistent" in msg


@requires_resource
def test_find_root_works() -> None:
    root = _find_wetext_root()
    assert (root / "zh" / "tn" / "tagger.fst").is_file()
    assert (root / "zh" / "tn" / "verbalizer.fst").is_file()


@requires_resource
def test_normalize_converts_digits() -> None:
    """核心功能：数字转中文读法。"""
    n = LocalWetextNormalizer(_find_wetext_root())
    out = n.normalize("今天是2026年11月9日")
    assert "二零二六" in out
    assert "十一月" in out
    assert "9" not in out.replace("九", "")


@requires_resource
def test_normalize_percent() -> None:
    n = LocalWetextNormalizer(_find_wetext_root())
    out = n.normalize("涨了100%")
    assert "百分之一百" in out


@requires_resource
def test_normalize_decimal() -> None:
    n = LocalWetextNormalizer(_find_wetext_root())
    out = n.normalize("涨了3.14倍")
    assert "三点一四" in out


@requires_resource
def test_normalize_skips_text_without_digits() -> None:
    """没有数字的文本应原样返回（与 wetext 行为一致）。"""
    n = LocalWetextNormalizer(_find_wetext_root())
    src = "今天天气真不错，我们一起出去走走吧。"
    assert n.normalize(src) == src


@requires_resource
def test_normalize_keeps_punctuation() -> None:
    n = LocalWetextNormalizer(_find_wetext_root())
    out = n.normalize("温度25度，湿度60%。")
    assert "，" in out or "。" in out


class _FakeFrontend:
    """模拟 CosyVoiceFrontEnd 的最小接口。

    注意：属性必须在 __init__ 里设成实例属性，
    不能用类属性——否则 hasattr() 会误判为「已有模型」。
    """

    def __init__(self) -> None:
        self.text_frontend = ""
        # 刻意不设 zh_tn_model / en_tn_model


@requires_resource
def test_enable_injects_frontend() -> None:
    fe = _FakeFrontend()
    assert enable_wetext_frontend(fe) is True
    assert fe.text_frontend == "wetext"
    assert fe.zh_tn_model is not None


@requires_resource
def test_enable_is_idempotent() -> None:
    """重复启用不应报错。"""
    fe = _FakeFrontend()
    assert enable_wetext_frontend(fe) is True
    assert enable_wetext_frontend(fe) is True
    assert fe.text_frontend == "wetext"


@requires_resource
def test_enable_force_overrides() -> None:
    fe = _FakeFrontend()
    fe.text_frontend = "ttsfrd"
    # 不 force 时保留原值
    assert enable_wetext_frontend(fe) is True
    assert fe.text_frontend == "ttsfrd"
    # force 时覆盖
    assert enable_wetext_frontend(fe, force=True) is True
    assert fe.text_frontend == "wetext"


@requires_resource
def test_frontend_status_shape() -> None:
    fe = _FakeFrontend()
    st = frontend_status(fe)
    assert "text_frontend" in st
    assert "cache_exists" in st
    assert st["cache_exists"] is True


def test_frontend_status_without_model() -> None:
    fe = _FakeFrontend()
    st = frontend_status(fe)
    assert st["text_frontend"] == ""
    assert st["has_zh_model"] is False
