"""启动器（launcher）单元测试。

## 为什么单独测

启动器是「服务都没起来时用来救场」的工具 —— 它自己出错就没人能救了。
但它又**不该在测试里真的去启动进程**（会拉起 GPU 模型、占端口、误杀用户服务）。
所以这里全部用替身：不 spawn、不联网、只验证**编排逻辑与判定规则**。

真正的端到端验证在：
- `docs/33_LAUNCHER.md` 的手工验收步骤（一键启动 → 端到端对话）
- `tests/probe_launcher_e2e.py`（可选，需要真实环境）
"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from launcher import orchestrator as orch_mod
from launcher.orchestrator import (
    SERVICES,
    Orchestrator,
    ServiceSpec,
    inspect_llm_env,
    port_listening,
    probe_health,
)

# ======================================================================
# 一、服务定义的自洽性
# ======================================================================


def test_dependencies_declared_before_use() -> None:
    """依赖必须先出现在列表前面，否则「一键启动」必然拿到未就绪的依赖。"""
    seen: set[str] = set()
    for spec in SERVICES:
        for dep in spec.depends_on:
            assert dep in seen, (
                f"服务 {spec.name} 依赖 {dep}，但 {dep} 排在它后面 —— "
                "串行启动时依赖必定未就绪"
            )
        seen.add(spec.name)


def test_service_ports_unique() -> None:
    """端口不能重复（重复会把对方健康检查打错）。"""
    used: dict[int, str] = {}
    for spec in SERVICES:
        if spec.port is None:
            continue
        assert spec.port not in used, (
            f"{spec.name} 与 {used[spec.port]} 都用了端口 {spec.port}"
        )
        used[spec.port] = spec.name


def test_tts_is_zjy14_engine_on_50001() -> None:
    """TTS 必须是 zjy14 的（:50001），不能是 zjy13 的 tts_server_wsl（:50000）。"""
    tts = next(s for s in SERVICES if s.name == "tts")
    assert tts.port == 50001, "TTS 端口不是 50001（zjy14 引擎）"
    assert tts.port != 50000, "TTS 指向了 zjy13 自带的 tts_server_wsl"
    assert tts.kind == "wsl", "TTS 应跑在 WSL"


def test_asr_is_standalone_service() -> None:
    """ASR 必须是独立进程（:50002），不再内嵌在网关里。"""
    asr = next(s for s in SERVICES if s.name == "asr")
    assert asr.port == 50002
    assert asr.kind == "win"


def test_gateway_depends_on_tts_and_asr() -> None:
    """网关必须等 TTS 与 ASR 就绪，否则它启动时会因 TTS 健康检查失败而退出。"""
    gw = next(s for s in SERVICES if s.name == "gateway")
    assert set(gw.depends_on) == {"tts", "asr"}


def test_llm_is_optional_probe() -> None:
    """LLM 是云端 API，没有本地进程，且不能因为它失败而判整条链路不可用。"""
    llm = next(s for s in SERVICES if s.name == "llm")
    assert llm.kind == "probe"
    assert llm.required is False


def test_required_services_cover_the_pipeline() -> None:
    """TTS/ASR/网关是必需的；缺任何一个板子都不通。"""
    required = {s.name for s in SERVICES if s.required}
    assert required == {"tts", "asr", "gateway"}


# ======================================================================
# 二、LLM 配置探测（纯文件解析，不联网）
# ======================================================================


def _write_env(tmp_path: Path, **kv: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(
        "\n".join(f"{k}={v}" for k, v in kv.items()) + "\n", encoding="utf-8"
    )
    return p


def test_inspect_llm_env_complete(tmp_path: Path) -> None:
    env = _write_env(
        tmp_path,
        LLM_BASE_URL="https://api.deepseek.com",
        LLM_API_KEY="sk-test-000000000000",
        LLM_MODEL="deepseek-flash",
        LLM_PROXY="http://127.0.0.1:7897",
    )
    info = inspect_llm_env(env)
    assert info["ok"] is True
    assert info["api_key_set"] is True
    assert info["model"] == "deepseek-flash"
    assert info["proxy_port"] == 7897


def test_inspect_llm_env_missing_key(tmp_path: Path) -> None:
    env = _write_env(
        tmp_path, LLM_BASE_URL="https://x", LLM_MODEL="m"
    )
    info = inspect_llm_env(env)
    assert info["ok"] is False
    assert any("LLM_API_KEY" in p for p in info["problems"])


def test_inspect_llm_env_missing_file(tmp_path: Path) -> None:
    info = inspect_llm_env(tmp_path / "nope.env")
    assert info["ok"] is False
    assert "不存在" in info["error"]


def test_inspect_llm_env_reports_proxy_warning(tmp_path: Path) -> None:
    """代理端口不通时只警告，不能因此判「配置不对」。"""
    env = _write_env(
        tmp_path,
        LLM_BASE_URL="https://api.deepseek.com",
        LLM_API_KEY="sk-x",
        LLM_MODEL="m",
        # 选一个几乎肯定没人监听的端口
        LLM_PROXY="http://127.0.0.1:9",
    )
    info = inspect_llm_env(env)
    assert info["ok"] is True, "代理不通不应判配置失败"
    assert info["proxy_up"] is False
    assert info["warnings"], "代理不通必须给出警告"


def test_inspect_llm_env_never_leaks_key(tmp_path: Path) -> None:
    """返回值里不能出现密钥内容（页面会展示它）。"""
    secret = "sk-super-secret-value-1234567890"
    env = _write_env(
        tmp_path, LLM_BASE_URL="https://x", LLM_API_KEY=secret, LLM_MODEL="m"
    )
    blob = json.dumps(inspect_llm_env(env), ensure_ascii=False)
    assert secret not in blob, "LLM 密钥被回显到了接口返回值里"


# ======================================================================
# 三、端口与健康探测
# ======================================================================


def test_port_listening_detects_real_listener() -> None:
    """起一个临时监听，port_listening 必须能探到。"""
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert port_listening(port) is True
    finally:
        srv.close()


def test_port_listening_false_for_closed() -> None:
    assert port_listening(9) is False


def test_probe_health_without_url() -> None:
    ok, payload = probe_health(None)
    assert ok is False
    assert "健康检查" in str(payload)


def test_probe_health_http_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """probe_health 认 200 + JSON；ok=False 的响应要判为不健康。"""

    class _Resp:
        status = 200

        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self, _n: int = -1) -> bytes:
            return self._body

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _Resp(b'{"ok": true}')
    )
    ok, payload = probe_health("http://127.0.0.1:1/health")
    assert ok is True and payload == {"ok": True}

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _Resp(b'{"ok": false}')
    )
    ok, _ = probe_health("http://127.0.0.1:1/health")
    assert ok is False, "ok=false 的健康检查必须判为不健康"


# ======================================================================
# 四、编排逻辑（全部用替身，不 spawn 进程）
# ======================================================================


def _probe_spec() -> ServiceSpec:
    return ServiceSpec(
        name="probe1",
        label="探测服务",
        kind="probe",
        description="测试用",
        required=False,
    )


def test_status_one_probe_uses_inspect(monkeypatch: pytest.MonkeyPatch) -> None:
    o = Orchestrator(specs=(_probe_spec(),))
    monkeypatch.setattr(
        orch_mod, "inspect_llm_env", lambda *a, **k: {"ok": True, "detail": "ok"}
    )
    st = o.status_one("probe1")
    assert st["healthy"] is True
    assert st["running"] is True


def test_status_one_probe_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    o = Orchestrator(specs=(_probe_spec(),))
    monkeypatch.setattr(
        orch_mod,
        "inspect_llm_env",
        lambda *a, **k: {"ok": False, "error": "缺 LLM_API_KEY"},
    )
    st = o.status_one("probe1")
    assert st["healthy"] is False


def test_status_skips_probe_when_port_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """端口没监听时不去打健康检查（避免每次轮询白等超时）。"""
    spec = ServiceSpec(
        name="svc",
        label="x",
        kind="win",
        description="x",
        port=9,
        health_url="http://127.0.0.1:9/health",
    )
    o = Orchestrator(specs=(spec,))
    called = {"n": 0}

    def _boom(*a: object, **k: object):
        called["n"] += 1
        raise AssertionError("端口未监听时不应该调用健康检查")

    monkeypatch.setattr(orch_mod, "probe_health", _boom)
    st = o.status_one("svc")
    assert st["healthy"] is False
    assert called["n"] == 0


def test_unknown_service_raises() -> None:
    o = Orchestrator()
    with pytest.raises(KeyError):
        o.status_one("no-such-service")
    with pytest.raises(KeyError):
        o.start("no-such-service")


def test_dry_run_does_not_spawn() -> None:
    """dry_run 下 start 必须只返回计划，不真的起进程。"""
    o = Orchestrator(dry_run=True)
    res = o.start("asr")
    assert res["ok"] is True
    assert res["action"] == "dry_run"
    assert o._procs == {}, "dry-run 竟然记了进程，说明真的 spawn 了"


def test_start_all_does_not_skip_independent_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tts 失败不能让 LLM 探测被「跳过」。

    这是 2026-11-26 修掉的编排缺陷：原实现用一个全局 failed 短路，
    任何服务失败都会把后面的全部标 skipped（连带无副作用的 LLM 探测）。
    """
    specs = (
        ServiceSpec("tts", "TTS", "win", "x", port=9, required=True),
        ServiceSpec("llm", "LLM", "probe", "y", required=False),
        ServiceSpec(
            "gateway", "网关", "win", "z", port=9, required=True,
            depends_on=("tts",),
        ),
    )
    o = Orchestrator(specs=specs)

    # tts 启动失败
    monkeypatch.setattr(
        o, "start",
        lambda name, **k: (
            {"ok": False, "name": name, "action": "failed", "message": "test-fail"}
            if name == "tts"
            else {"ok": True, "name": name, "action": "probe", "message": "probed"}
        ),
    )
    monkeypatch.setattr(
        orch_mod, "inspect_llm_env", lambda *a, **k: {"ok": True, "detail": "ok"}
    )
    monkeypatch.setattr(orch_mod, "port_listening", lambda *a, **k: False)

    out = o.start_all()
    actions = {r["name"]: r["action"] for r in out["results"]}
    assert actions["llm"] == "probe", (
        f"LLM 探测被跳过了（actions={actions}）—— "
        "无副作用的探测不该因别的服务失败而短路"
    )
    assert actions["gateway"] == "blocked", "依赖 tts 失败，网关应被 blocked 而非启动"
    assert out["ok"] is False
    assert "tts" in out["failed_required"]


def test_start_all_ok_when_all_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    specs = (
        ServiceSpec("tts", "TTS", "win", "x", port=9, required=True),
        ServiceSpec("llm", "LLM", "probe", "y", required=False),
    )
    o = Orchestrator(specs=specs)
    monkeypatch.setattr(
        o, "start", lambda name, **k: {"ok": True, "name": name, "action": "skip"}
    )
    monkeypatch.setattr(
        orch_mod, "inspect_llm_env", lambda *a, **k: {"ok": True, "detail": "ok"}
    )
    monkeypatch.setattr(orch_mod, "port_listening", lambda *a, **k: False)
    out = o.start_all()
    assert out["ok"] is True
    assert out["failed_required"] == []


def test_stop_unknown_service_raises() -> None:
    o = Orchestrator()
    with pytest.raises(KeyError):
        o.stop("nope")


def test_stop_probe_is_noop() -> None:
    """LLM 是云端，停止它应返回「无进程可停」而不是报错。"""
    o = Orchestrator()
    res = o.stop("llm")
    assert res["ok"] is True
    assert "云端" in res["message"]


# ======================================================================
# 五、日志解码（WSL 输出 UTF-16 的坑）
# ======================================================================


def test_read_log_decodes_utf16le(tmp_path: Path) -> None:
    """wsl.exe 默认输出 UTF-16LE；日志页不能显示成方块。"""
    p = tmp_path / "tts.log"
    p.write_bytes("你好 WSL\n第二行".encode("utf-16-le"))
    o = Orchestrator(log_dir=tmp_path)
    text = o.read_log("tts", lines=10)
    assert "你好 WSL" in text, f"UTF-16 日志没有正确解码：{text!r}"
    assert "第二行" in text


def test_read_log_decodes_utf8(tmp_path: Path) -> None:
    p = tmp_path / "asr.log"
    p.write_text("普通 UTF-8 日志\n完成", encoding="utf-8")
    o = Orchestrator(log_dir=tmp_path)
    text = o.read_log("asr", lines=10)
    assert "普通 UTF-8 日志" in text


def test_read_log_limits_lines(tmp_path: Path) -> None:
    p = tmp_path / "x.log"
    p.write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
    o = Orchestrator(log_dir=tmp_path)

    class _S(ServiceSpec):
        pass

    o._by_name["x"] = ServiceSpec("x", "x", "probe", "x")  # 便于 read_log 校验名字
    text = o.read_log("x", lines=5)
    assert len(text.splitlines()) == 5


# ======================================================================
# 六、HTTP 层（起真实 server，只测只读端点）
# ======================================================================


def test_http_serves_page_and_specs(tmp_path: Path) -> None:
    """控制台页面与 /api/specs 必须可直接访问。"""
    from launcher.server import create_server

    o = Orchestrator(log_dir=tmp_path, dry_run=True)
    httpd = create_server("127.0.0.1", 0, o)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            html = r.read().decode("utf-8")
        assert "一键启动" in html, "控制台首页没有渲染出启动按钮"

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/specs", timeout=5
        ) as r:
            data = json.loads(r.read().decode("utf-8"))
        assert data["ok"] is True
        names = [s["name"] for s in data["services"]]
        assert names == [s.name for s in SERVICES]
        assert data["engine"] == "cosyvoice3"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_rejects_unknown_path(tmp_path: Path) -> None:
    from launcher.server import create_server

    o = Orchestrator(log_dir=tmp_path, dry_run=True)
    httpd = create_server("127.0.0.1", 0, o)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/nope", timeout=5)
        assert ei.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()