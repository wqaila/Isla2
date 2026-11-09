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
