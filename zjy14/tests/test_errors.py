"""异常 → HTTP 状态码映射测试。"""

from __future__ import annotations

import pytest

from custom_voice.core import errors


@pytest.mark.parametrize(
    ("exc_cls", "status", "code"),
    [
        (errors.ConfigError, 500, "config_error"),
        (errors.EngineError, 500, "engine_error"),
        (errors.EngineNotReadyError, 503, "engine_not_ready"),
        (errors.SynthesisError, 500, "synthesis_error"),
        (errors.QueueTimeoutError, 503, "queue_timeout"),
        (errors.InvalidRequestError, 400, "invalid_request"),
        (errors.SpeakerError, 500, "speaker_error"),
        (errors.SpeakerNotFoundError, 404, "speaker_not_found"),
        (errors.AudioQualityError, 422, "audio_quality_error"),
        (errors.VRAMError, 507, "vram_insufficient"),
    ],
)
def test_exception_status_mapping(exc_cls, status: int, code: str) -> None:
    exc = exc_cls("boom")
    assert exc.http_status == status
    assert exc.code == code


def test_to_dict_shape() -> None:
    exc = errors.InvalidRequestError("bad text", detail={"field": "text"})
    d = exc.to_dict()
    assert d["code"] == "invalid_request"
    assert d["message"] == "bad text"
    assert d["detail"] == {"field": "text"}


def test_to_dict_without_detail() -> None:
    d = errors.EngineNotReadyError("not ready").to_dict()
    assert "detail" not in d


def test_all_errors_inherit_base() -> None:
    for name in errors.__all__:
        cls = getattr(errors, name)
        assert issubclass(cls, errors.ZJY14Error), name


def test_queue_timeout_is_retryable_503() -> None:
    """排队超时必须映射 503，且**不能**被当成正常结果。

    2026-11-19 缺陷回归锁：

    ``synthesize_stream`` 原先在排队超时时 yield 一个「长度 0」的结束标记
    就正常返回（HTTP 200 + 空音频）。zjy13 的 ``_iter_length_prefixed``
    把长度 0 理解为**正常完成**，于是：

      - 板子听到静默
      - 日志没有任何错误
      - 调用方当作合成成功继续收尾

    典型静默失败。修法是抛异常 → 503，让 zjy13 抛 TTSUnavailableError。

    这里断言两件事：
    1. 状态码是 503（zjy13 对 503 有专门分支）
    2. 它是 EngineError 子类（与「暂时不可用」语义一致）
    """
    exc = errors.QueueTimeoutError("队列已满")
    assert exc.http_status == 503, "排队超时必须 503，否则 zjy13 不会报错"
    assert exc.code == "queue_timeout"
    assert isinstance(exc, errors.EngineError)
    assert isinstance(exc, errors.ZJY14Error)
