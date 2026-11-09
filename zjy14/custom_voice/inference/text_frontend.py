"""让 CosyVoice 启用 wetext 文本前端，无需 modelscope token。

问题根因（已实测确认）：
  wetext/wetext.py:38 无条件调用 snapshot_download("pengzhendong/wetext")，
  该调用每次都要联网校验，无 token 时返回 403，导致
  cosyvoice/cli/frontend.py 的三级回退降级到「无前端」。

  但 fst 资源其实早在 ~/.cache/modelscope/hub/pengzhendong/wetext 里，
  是之前有 token 或网络可用时下载好的。

解决方案：
  不修改上游代码，也不改 wetext 包。而是提供一个
  patch_frontend() 函数，在创建 CosyVoice 实例后，
  用本地 fst 直接构造 wetext.Normalizer 并注入 frontend。

  好处：
  - 零侵入：不碰 ~/CosyVoice 一行代码
  - 无网络依赖：不需要 token，不需要联网
  - 可回退：失败时明确报错，不静默降级

用法：
    from custom_voice.inference.text_frontend import enable_wetext_frontend
    cosy = CosyVoice3(MODEL)
    ok = enable_wetext_frontend(cosy.frontend)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.logging import get_logger

logger = get_logger("inference.text_frontend")

#: modelscope 缓存中的 wetext 资源默认位置
DEFAULT_WETEXT_CACHE = Path.home() / ".cache" / "modelscope" / "hub" / "pengzhendong" / "wetext"


class TextFrontendError(RuntimeError):
    """文本前端启用失败。"""


def _find_wetext_root(explicit: Path | None = None) -> Path:
    """定位 wetext 资源目录。"""
    candidates = []
    if explicit is not None:
        candidates.append(explicit)
    candidates.extend([
        DEFAULT_WETEXT_CACHE,
        Path.home() / ".cache" / "modelscope" / "hub" / "models" / "pengzhendong" / "wetext",
    ])

    for p in candidates:
        if (p / "zh" / "tn" / "tagger.fst").is_file() and (
            p / "zh" / "tn" / "verbalizer.fst"
        ).is_file():
            return p

    raise TextFrontendError(
        "找不到 wetext 的 fst 资源。已查找：\n"
        + "\n".join(f"  - {c}" for c in candidates)
        + "\n\n请确认是否曾成功下载过 wetext（需要一次联网或 modelscope token）。"
        "下载后的资源会自动缓存在上述位置。"
    )


def build_normalizers(root: Path) -> tuple[Any, Any]:
    """用本地 fst 构造中英文 Normalizer。

    复刻 wetext.Normalizer 的逻辑，但跳过 snapshot_download。
    """
    try:
        from kaldifst import TextNormalizer as normalizer
    except ImportError as exc:
        raise TextFrontendError(
            "kaldifst 未安装。wetext 依赖它做 FST 运算：pip install kaldifst"
        ) from exc

    tagger = normalizer(str(root / "zh" / "tn" / "tagger.fst"))
    verbalizer = normalizer(str(root / "zh" / "tn" / "verbalizer.fst"))
    return tagger, verbalizer


class LocalWetextNormalizer:
    """与 wetext.Normalizer 接口兼容的本地实现。

    只暴露 CosyVoice frontend 实际用到的 normalize() 方法。
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._zh_tagger, self._zh_verbalizer = build_normalizers(root)
        self._en_tagger = None
        self._en_verbalizer = None
        # 英文资源存在才加载（中文场景非必需）
        en_tagger = root / "en" / "tn" / "tagger.fst"
        en_verb = root / "en" / "tn" / "verbalizer.fst"
        if en_tagger.is_file() and en_verb.is_file():
            try:
                from kaldifst import TextNormalizer as normalizer

                self._en_tagger = normalizer(str(en_tagger))
                self._en_verbalizer = normalizer(str(en_verb))
            except Exception:  # noqa: BLE001
                logger.warning("英文 TN 加载失败，仅使用中文")

    @staticmethod
    def _contains_chinese(s: str) -> bool:
        return any("\u4e00" <= c <= "\u9fff" for c in s)

    def normalize(self, text: str) -> str:
        """与 wetext.Normalizer.normalize 行为一致：只在含数字时才处理。"""
        import re

        if not re.search(r"\d", text):
            return text
        if self._contains_chinese(text):
            tagged = self._zh_tagger(text)
            return self._zh_verbalizer(tagged)
        if self._en_tagger is not None and self._en_verbalizer is not None:
            tagged = self._en_tagger(text)
            return self._en_verbalizer(tagged)
        return text


def enable_wetext_frontend(
    frontend: Any,
    *,
    root: Path | None = None,
    force: bool = False,
) -> bool:
    """给 CosyVoice 的 frontend 注入 wetext 文本前端。

    :param frontend: ``cosy.frontend``（CosyVoiceFrontEnd 实例）
    :param root: wetext 资源目录，默认自动探测
    :param force: 即使已有前端也覆盖
    :return: 是否成功启用
    :raises TextFrontendError: 资源缺失或依赖缺失
    """
    current = getattr(frontend, "text_frontend", "")
    if current and not force:
        logger.info(
            "文本前端已启用，跳过",
            extra={"extra_fields": {"current": current}},
        )
        return True

    wetext_root = _find_wetext_root(root)
    zh = LocalWetextNormalizer(wetext_root)

    # CosyVoice frontend 用 zh_tn_model 做中文规范化
    frontend.zh_tn_model = zh
    frontend.text_frontend = "wetext"
    if zh._en_tagger is not None:  # noqa: SLF001
        frontend.en_tn_model = zh

    logger.info(
        "已启用 wetext 文本前端（本地 fst，无需 token）",
        extra={
            "extra_fields": {
                "root": str(wetext_root),
                "zh": True,
                "en": zh._en_tagger is not None,  # noqa: SLF001
            }
        },
    )
    return True


def frontend_status(frontend: Any) -> dict[str, Any]:
    """返回文本前端状态，供 /health 与诊断脚本使用。"""
    return {
        "text_frontend": getattr(frontend, "text_frontend", ""),
        "has_zh_model": hasattr(frontend, "zh_tn_model"),
        "has_en_model": hasattr(frontend, "en_tn_model"),
        "wetext_cache": str(DEFAULT_WETEXT_CACHE),
        "cache_exists": (DEFAULT_WETEXT_CACHE / "zh" / "tn" / "tagger.fst").is_file(),
    }


__all__ = [
    "DEFAULT_WETEXT_CACHE",
    "LocalWetextNormalizer",
    "TextFrontendError",
    "build_normalizers",
    "enable_wetext_frontend",
    "frontend_status",
]
