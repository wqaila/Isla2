"""独立 ASR 服务（`server/asr_service.py`）的单元测试。

## 为什么不用真模型

测试要能秒级跑完、且不占显存。这里注入一个**假引擎**，
只验证 HTTP 层与请求/响应契约 —— 真实识别效果由手工验收覆盖
（`POST :50002/transcribe` 传一段 wav）。

## 覆盖点

1. `/health` 在未加载时如实报 `loaded=false`，加载后返回设备信息
2. `/transcribe` 接受**原始 WAV 字节**，返回 text
3. `/transcribe` 接受 `{"audio_path": ...}`
4. 缺 `audio_path` → 400（不能静默成功）
5. 过大的请求体 → 413
6. 引擎加载失败 → 503（而不是 500 或静默）
7. 未知路径 → 404
8. CORS 预检可用（zjy14 前端跨端口调用需要）
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from server import asr_service


class _FakeDevice:
    device_name = "FakeGPU"
    using_gpu = True
    torch_version = "2.8.0+cu128"
    cuda_version = "12.8"
    arch_match = True


class _FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "audio_duration_s": 1.0,
            "infer_ms": 12.0,
            "total_ms": 20.0,
            "realtime_factor": 0.012,
        }


class _FakeEngine:
    """替身：不加载 funasr，只记录被调用的路径。"""

    model_id = "fake/paraformer"
    device_info = _FakeDevice()
    load_ms = 1.0
    warmup_ms = 0.5

    def __init__(self) -> None:
        self.paths: list[Path] = []

    def transcribe_file(self, path) -> _FakeResult:  # noqa: ANN001
        p = Path(path)
        assert p.is_file(), "传给引擎的路径必须存在"
        self.paths.append(p)
        return _FakeResult("你好世界")


def _holder_with_fake_engine() -> tuple[asr_service._EngineHolder, _FakeEngine]:
    holder = asr_service._EngineHolder(preload=False, use_vad=False, device="cpu")
    fake = _FakeEngine()
    # 直接注入，绕过 ensure_loaded（那会 import funasr 并加载真模型）
    holder._engine = fake
    return holder, fake


@pytest.fixture()
def server_port():
    holder, fake = _holder_with_fake_engine()
    httpd = asr_service.create_server("127.0.0.1", 0, holder)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield port, fake
    finally:
        httpd.shutdown()
        httpd.server_close()


def _post(port: int, body: bytes, ctype: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/transcribe",
        data=body,
        headers={"Content-Type": ctype},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_health_reports_loaded(server_port) -> None:  # noqa: ANN001
    port, _ = server_port
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/health", timeout=5
    ) as r:
        data = json.loads(r.read().decode("utf-8"))
    assert data["ok"] is True
    assert data["kind"] == "zjy13-asr-service"
    assert data["loaded"] is True
    assert data["device"] == "FakeGPU"
    assert data["using_gpu"] is True
    assert data["model"] == "fake/paraformer"


def test_health_before_load() -> None:
    """未加载时必须如实报 loaded=false（前端据此显示「加载中」）。"""
    holder = asr_service._EngineHolder(preload=False, use_vad=False, device="cpu")
    info = holder.health()
    assert info["loaded"] is False
    assert info["ok"] is True  # 服务活着，只是模型还没加载


def test_transcribe_raw_wav_bytes(server_port) -> None:  # noqa: ANN001
    port, fake = server_port
    status, data = _post(port, b"RIFF....fake-wav", "audio/wav")
    assert status == 200
    assert data["ok"] is True
    assert data["text"] == "你好世界"
    assert data["service_ms"] >= 0
    assert len(fake.paths) == 1


def test_transcribe_audio_path_json(server_port, tmp_path: Path) -> None:  # noqa: ANN001
    port, fake = server_port
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"RIFF")
    body = json.dumps({"audio_path": str(wav)}).encode("utf-8")
    status, data = _post(port, body, "application/json")
    assert status == 200
    assert data["text"] == "你好世界"
    assert fake.paths and fake.paths[-1] == wav


def test_transcribe_missing_audio_path(server_port) -> None:  # noqa: ANN001
    """缺字段必须 400 —— 不能静默返回 ok。"""
    port, _ = server_port
    status, data = _post(port, b"{}", "application/json")
    assert status == 400
    assert data["ok"] is False
    assert "audio_path" in data["error"]


def test_transcribe_empty_body(server_port) -> None:  # noqa: ANN001
    port, _ = server_port
    status, data = _post(port, b"", "audio/wav")
    assert status == 400
    assert data["ok"] is False


def test_transcribe_rejects_too_large(server_port) -> None:  # noqa: ANN001
    """超过上限必须 413，而不是读进内存再爆。"""
    port, _ = server_port
    big = b"x" * (asr_service.MAX_BODY_BYTES + 1)
    status, data = _post(port, big, "audio/wav")
    assert status == 413
    assert data["ok"] is False


def test_transcribe_engine_load_failure_is_503() -> None:
    """引擎加载失败必须 503（可重试），且把原因带出来。"""
    holder = asr_service._EngineHolder(preload=False, use_vad=False, device="cpu")
    holder._engine_error = "CUDANotAvailableError: 无可用 GPU"

    httpd = asr_service.create_server("127.0.0.1", 0, holder)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        status, data = _post(port, b"RIFF", "audio/wav")
        assert status == 503
        assert "CUDANotAvailable" in data["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_unknown_path_404(server_port) -> None:  # noqa: ANN001
    port, _ = server_port
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
    assert ei.value.code == 404


def test_cors_preflight(server_port) -> None:  # noqa: ANN001
    """zjy14 前端（不同端口）调本服务需要 CORS。"""
    port, _ = server_port
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/transcribe", method="OPTIONS"
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.headers.get("Access-Control-Allow-Origin") == "*"


def test_temp_file_under_engine_cache_dir(tmp_path: Path) -> None:
    """临时文件必须落在引擎缓存目录，不能污染仓库根。

    实测踩过：``tempfile.gettempdir()`` 在部分环境下会回退到**当前工作目录**，
    导致每次 /transcribe 都在仓库根留下 ``asr_svc_*`` 目录。
    """
    holder = asr_service._EngineHolder(preload=False, use_vad=False, device="cpu")

    class _E(_FakeEngine):
        cache_dir = tmp_path

    engine = _E()
    holder._engine = engine
    holder.transcribe_wav_bytes(b"RIFF....data")
    # 调用结束后临时目录应已清理
    leftovers = list((tmp_path / "_work").glob("asr_svc_*"))
    assert not leftovers, f"临时目录没清理：{leftovers}"
    assert engine.paths, "引擎没被调用"