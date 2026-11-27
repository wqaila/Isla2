"""语音链路服务编排：启动 / 停止 / 健康检查。

## 服务的真实拓扑

板子的完整链路是三段 + 一个网关，跨 Windows 与 WSL 两侧：

    ESP32 ──WS──▶ zjy13 网关(:8765)
                      ├─ ASR  → ASR 独立服务(:50002)      [Windows / .venv-asr]
                      ├─ LLM  → DeepSeek 云 API（+可选本地代理 7897）
                      └─ TTS  → zjy14 引擎(:50001)         [WSL / cosyvoice-venv]

**TTS 必须是 zjy14 的**：网关启动时显式注入
``TTS_BASE_URL=http://127.0.0.1:50001``（``load_dotenv(override=False)``
保证进程环境变量优先于 zjy13/.env 里的 :50000），
这样网关调的是 zjy14 的 CosyVoice3，而不是 zjy13 自带的 tts_server_wsl。

**ASR 是独立进程**：网关用 ``--asr-url`` 指向它，本进程不再加载 FunASR，
同一份模型不会在显存里驻留两次，ASR 也能单独重启/单测。

## 为什么分 Windows / WSL

- ASR 与网关跑 Windows（``zjy13/.venv-asr``，FunASR + CUDA）
- TTS 跑 WSL（CosyVoice3 依赖 deepspeed / onnxruntime-gpu，官方只支持 Linux）

所以本编排器必须跑在 **Windows** 侧，用 ``wsl.exe`` 拉起 TTS。

## 只依赖标准库

启动器是「服务都没起来时用来救场」的工具，自身不能依赖 FastAPI/torch。
本模块只用 ``subprocess`` / ``socket`` / ``urllib`` / ``json``。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------
# 路径
# ----------------------------------------------------------------------

#: 本文件位于 zjy14/launcher/orchestrator.py
ZJY14_ROOT = Path(__file__).resolve().parent.parent
#: 工作区根（含 zjy13 与 zjy14）
WORKSPACE = ZJY14_ROOT.parent
ZJY13_ROOT = WORKSPACE / "zjy13"

#: 日志目录
LOG_DIR = ZJY14_ROOT / "logs" / "launcher"

#: WSL 发行版名（与 README / 启动脚本一致）
WSL_DISTRO = os.environ.get("ZJY14_WSL_DISTRO", "Ubuntu-24.04")

#: 是否 Windows 平台。
#:
#: 抽成模块常量（而不是直接写 `sys.platform`）是为了让测试能替换它 ——
#: 测试套件跑在 WSL 的 Linux Python 里，否则永远走不到 Windows 分支。
IS_WINDOWS = sys.platform == "win32"

#: **绝对不能杀**的进程名（小写，去掉 .exe）。
#:
#: 为什么要有这个名单（2026-11-27 实测事故）：
#: WSL 里的服务（TTS）在 Windows 上看到的监听者并不是它自己，而是 WSL2 的
#: 端口转发进程 ``wslrelay``。当时的「按端口兜底杀」把它 taskkill 了，结果：
#:   1. TTS **根本没停**（WSL 内 uvicorn 继续跑，继续占 5.7GB 显存）；
#:   2. 界面却显示「已终止占用端口 50001 的进程」——**假成功**；
#:   3. 破坏 WSL 的 localhost 转发（其它端口也可能受影响），
#:      而且 WSL 会立刻把它重新拉起来，所以端口照样在监听。
#:
#: 结论：这些进程只能由 WSL 自己管理，外部 kill 一律跳过。
NEVER_KILL_PROCESS_NAMES = frozenset(
    {
        # WSL 端口转发 / 虚拟化
        "wslrelay",
        "wslhost",
        "wslservice",
        "wsl",
        "vmmem",
        "vmmemwsl",
        "vmwp",
        "vmcompute",
        "vmms",
        # Windows 关键系统进程（防御性：万一 PID 解析错位也不至于搞坏系统）
        "system",
        "system idle process",
        "services",
        "lsass",
        "wininit",
        "csrss",
        "smss",
    }
)


def zjy13_python() -> Path:
    """zjy13 的 ASR 侧 Python（FunASR + websockets 都在这个环境里）。"""
    return ZJY13_ROOT / ".venv-asr" / "Scripts" / "python.exe"


# ----------------------------------------------------------------------
# 服务定义
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    """一个可启动的服务。"""

    name: str
    label: str
    kind: str  # "win" | "wsl" | "probe"
    description: str
    port: int | None = None
    health_url: str | None = None
    required: bool = True
    #: 启动顺序依赖（这些服务健康后才启动本服务）
    depends_on: tuple[str, ...] = ()
    #: 健康检查的期望时长上限（秒）
    ready_timeout_s: float = 60.0


#: 服务表（顺序即「一键启动」的顺序）。
SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec(
        name="tts",
        label="TTS · zjy14 引擎",
        kind="wsl",
        description=(
            "CosyVoice3（zjy14 的 TTS）。跑在 WSL，端口 50001。"
            "网关会被指向它，而不是 zjy13 自带的 tts_server_wsl。"
        ),
        port=50001,
        health_url="http://127.0.0.1:50001/health",
        ready_timeout_s=180.0,  # 首次要加载模型 + 预热
    ),
    ServiceSpec(
        name="asr",
        label="ASR · FunASR Paraformer",
        kind="win",
        description=(
            "独立 ASR 服务（zjy13/server/asr_service.py），端口 50002。"
            "网关通过 --asr-url 调用它，不再本地加载模型。"
        ),
        port=50002,
        health_url="http://127.0.0.1:50002/health",
        ready_timeout_s=120.0,
    ),
    ServiceSpec(
        name="llm",
        label="LLM · DeepSeek 云端",
        kind="probe",
        description=(
            "云端 API（无需本地进程）。本项只做配置与连通性检查："
            "读取 zjy13/.env 的 LLM_API_KEY，并在配置了 LLM_PROXY 时探测代理端口。"
        ),
        port=None,
        health_url=None,
        required=False,
    ),
    ServiceSpec(
        name="gateway",
        label="网关 · zjy13 WS",
        kind="win",
        description=(
            "zjy13 WebSocket 网关（:8765），把板子的录音串起 ASR→LLM→TTS。"
            "启动时注入 TTS_BASE_URL=:50001 与 ASR_SERVICE_URL=:50002。"
        ),
        port=8765,
        health_url="http://127.0.0.1:8765/api/devices/health",
        depends_on=("tts", "asr"),
        ready_timeout_s=120.0,
    ),
)

SPEC_BY_NAME: dict[str, ServiceSpec] = {s.name: s for s in SERVICES}


# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------


def port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    """端口是否在监听（TCP connect 探测，比解析 netstat 可靠）。

    ``timeout`` 取 0.2s 而非 0.5s：实测 Windows 上对本机**未监听**的端口
    发起 connect 不会立刻 ECONNREFUSED，而是要挂到超时才返回。
    状态页每次要探测 4~5 个端口，0.5s 会让一次刷新耗时 2 秒以上，
    0.2s 足够（正常监听时 connect 是毫秒级返回）。
    """
    if not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_health(
    url: str | None, timeout: float = 2.0
) -> tuple[bool, dict[str, Any] | str | None]:
    """GET 一个健康检查端点。

    返回 ``(ok, payload)``；``ok`` 只表示「HTTP 200 且 JSON 里没有 ok=False」。
    失败时 payload 是错误字符串（便于前端直接显示原因）。
    """
    if not url:
        return False, "该服务没有健康检查端点"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read(4096)
            code = resp.status
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"

    if code != 200:
        return False, f"HTTP {code}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return True, None
    # zjy14 /health 在模型未就绪时返回 200 + ok=False 的情况也一并算不健康
    ok = payload.get("ok", True) if isinstance(payload, dict) else True
    return bool(ok), payload


def _tail(path: Path, lines: int = 40) -> str:
    """读日志尾部（文件可能不存在或很大）。

    **编码**：``wsl.exe`` 默认按 UTF-16LE 输出（实测日志里满是
    ``\x00`` 间隔的乱码），服务端日志又是 UTF-8。这里先按 BOM/零字节
    嗅探编码，再解码，避免控制台里显示成一堆方块。
    """
    if not path.is_file():
        return ""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            # 只回读最后 64KB，避免大文件把内存吃满
            back = min(size, 64 * 1024)
            fh.seek(size - back)
            data = fh.read()
    except OSError as exc:
        return f"[读取日志失败] {exc}"

    text: str
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        # 有 BOM：交给 utf-16 自己判断字节序
        text = data.decode("utf-16", "replace")
    elif data.count(b"\x00") > len(data) // 8:
        # 无 BOM 但大量 0x00：典型 UTF-16LE ASCII 混排
        text = data.decode("utf-16-le", "replace")
    else:
        text = data.decode("utf-8", "replace")
    # 去掉 NUL 残渣（截断在奇数边界时可能出现）
    text = text.replace("\x00", "")
    return "\n".join(text.splitlines()[-lines:])


# ----------------------------------------------------------------------
# LLM 配置探测（无进程）
# ----------------------------------------------------------------------


def inspect_llm_env(env_path: Path | None = None) -> dict[str, Any]:
    """读取 zjy13/.env 的 LLM 配置，判断「能不能用」。

    为什么不做真实 API 调用：那会消耗配额、也可能被网络/代理卡住数秒。
    这里只做**静态判定 + 代理端口探测**，把「配没配」与「代理通不通」
    分开展示；真要打网络由前端「测试连通性」按钮单独触发。
    """
    env_file = env_path or (ZJY13_ROOT / ".env")
    values: dict[str, str] = {}
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
        except (OSError, UnicodeDecodeError) as exc:
            return {
                "ok": False,
                "error": f"读取 {env_file} 失败: {exc}",
                "env_path": str(env_file),
            }
    else:
        return {
            "ok": False,
            "error": f"配置文件不存在: {env_file}",
            "env_path": str(env_file),
        }

    api_key = values.get("LLM_API_KEY", "")
    base_url = values.get("LLM_BASE_URL", "")
    model = values.get("LLM_MODEL", "")
    proxy = values.get("LLM_PROXY", "")

    problems: list[str] = []
    if not api_key:
        problems.append("LLM_API_KEY 未配置")
    if not base_url:
        problems.append("LLM_BASE_URL 未配置")
    if not model:
        problems.append("LLM_MODEL 未配置")

    proxy_port: int | None = None
    if proxy:
        try:
            from urllib.parse import urlparse

            proxy_port = urlparse(proxy).port
        except Exception:  # noqa: BLE001
            proxy_port = None

    proxy_up = port_listening(proxy_port) if proxy_port else True

    warnings: list[str] = []
    if proxy and not proxy_up:
        warnings.append(
            f"本地代理 {proxy} 未监听（LLM_PROXY_FALLBACK="
            f"{values.get('LLM_PROXY_FALLBACK', '?')}）"
        )

    return {
        "ok": not problems,
        "env_path": str(env_file),
        "base_url": base_url,
        "model": model,
        "api_key_set": bool(api_key),
        "proxy": proxy or None,
        "proxy_port": proxy_port,
        "proxy_up": proxy_up,
        "problems": [p for p in problems if p],
        "warnings": warnings,
        "detail": (
            f"{model} @ {base_url}"
            + (f"（代理 {proxy}）" if proxy else "（直连）")
        ),
    }


# ----------------------------------------------------------------------
# 编排器
# ----------------------------------------------------------------------


@dataclass(slots=True)
class _Proc:
    """本启动器拉起的子进程。"""

    popen: subprocess.Popen | None
    log_path: Path
    started_at: float
    port: int | None


class Orchestrator:
    """管理全部语音链路服务。"""

    def __init__(
        self,
        *,
        specs: tuple[ServiceSpec, ...] = SERVICES,
        log_dir: Path = LOG_DIR,
        dry_run: bool = False,
    ) -> None:
        self._specs = specs
        self._by_name = {s.name: s for s in specs}
        self._log_dir = log_dir
        self._procs: dict[str, _Proc] = {}
        self._dry_run = dry_run
        #: WSL 可达性探测结果（None = 还没探测过）
        self._wsl_probe: bool | None = None

    # ---- 只读 ----

    @property
    def specs(self) -> tuple[ServiceSpec, ...]:
        return self._specs

    def spec(self, name: str) -> ServiceSpec:
        if name not in self._by_name:
            raise KeyError(f"未知服务 {name!r}，可用：{', '.join(self._by_name)}")
        return self._by_name[name]

    # ---- 状态 ----

    def status_one(self, name: str) -> dict[str, Any]:
        spec = self.spec(name)
        proc = self._procs.get(name)

        pid: int | None = None
        managed = False
        exited = True
        if proc is not None and proc.popen is not None:
            pid = proc.popen.pid
            managed = True
            exited = proc.popen.poll() is not None

        # 「在跑」的判定：本启动器拉起的进程还活着，或端口在监听
        # （端口在监听覆盖了「用户手动起过 / 启动器重启后接管」的情况）
        running = (managed and not exited) or port_listening(spec.port or 0)

        healthy = False
        health_payload: Any = None
        if spec.kind == "probe":
            info = inspect_llm_env()
            # LLM 是云端 API：只要配置齐全就算「就绪」。
            # 代理没起来只是**警告**（LLM_PROXY_FALLBACK=true 时可直连），
            # 不能因此判整条链路不可用 —— 那会把「代理没开」误报成「服务坏了」。
            healthy = bool(info.get("ok"))
            health_payload = info
            running = healthy
        elif spec.health_url:
            # 端口没监听时不去打健康检查：Windows 上对已关闭的端口
            # 发起 connect 可能挂到超时（实测 2s），每次轮询都白等。
            if port_listening(spec.port or 0):
                healthy, health_payload = probe_health(spec.health_url)
            else:
                healthy, health_payload = False, "端口未监听"

        detail = spec.description
        if spec.kind == "probe" and isinstance(health_payload, dict):
            detail = str(health_payload.get("detail") or detail)
        elif managed and exited:
            code = proc.popen.returncode if proc.popen else "?"
            detail = f"进程已退出（退出码 {code}），见日志"

        return {
            "name": name,
            "label": spec.label,
            "kind": spec.kind,
            "port": spec.port,
            "required": spec.required,
            "depends_on": list(spec.depends_on),
            "running": bool(running),
            "managed": managed,
            "healthy": bool(healthy),
            "pid": pid,
            "detail": detail,
            "health": health_payload,
            "log_path": str(proc.log_path) if proc else str(
                self._log_dir / f"{name}.log"
            ),
        }

    def status_all(self) -> dict[str, Any]:
        items = [self.status_one(s.name) for s in self._specs]
        ready = all(
            it["healthy"] for it in items if it["required"]
        )
        return {
            "ok": True,
            "ready": ready,
            "services": items,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            # 环境能力：本进程能否访问 WSL。
            # 不能时前端要**提前**告诉用户「TTS 的启停请用脚本」，
            # 而不是等用户点了按钮再失败（实测有些环境确实禁止）。
            "wsl_available": self.wsl_available(),
        }

    def wsl_available(self) -> bool:
        """本进程能否通过 wsl.exe 访问 WSL（带缓存）。

        ## 为什么要探测

        实测（2026-11-27）：某些受限环境下，**Python 及其所有子进程**调用
        WSL 一律返回 `Wsl/E_ACCESSDENIED`，而同样的命令在用户的 PowerShell
        里正常。已排除的可能：

        | 方式 | 结果 |
        |---|---|
        | `wsl.exe`（管道 / 文件 / 新控制台 / 继承 stdio，四种都试） | 拒绝 |
        | 经 `cmd.exe` 转一层 | 拒绝 |
        | 经 `powershell.exe -Command` / `-File` | 拒绝 |
        | `ctypes` 直调 `wslapi.dll` 的 `WslLaunch` | 拒绝（0x80070005） |
        | `schtasks` 建一次性任务跳出进程树 | `schtasks` 本身「找不到路径」 |

        结论：限制在进程令牌层面，Python 侧无法自救。
        所以这里只做**探测**，让界面提前告知用户该用哪个脚本，
        而不是每次点按钮都失败一次。
        """
        if self._wsl_probe is None:
            self._wsl_probe = self._probe_wsl()
        return self._wsl_probe

    def _probe_wsl(self) -> bool:
        """跑一次极轻的命令判断 WSL 是否可达。"""
        if not IS_WINDOWS:  # pragma: no cover
            # 非 Windows：服务直接跑在本机，不需要 WSL
            return sys.platform.startswith("linux")
        try:
            res = subprocess.run(  # noqa: S603,S607
                ["wsl.exe", "-d", WSL_DISTRO, "-e", "bash", "-lc", "echo ZJY14_OK"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25,
                # Linux 上 subprocess 没有 CREATE_NO_WINDOW，必须 getattr 兜底
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return "ZJY14_OK" in (res.stdout or "")

    # ---- 启动 ----

    def _open_log(self, name: str):  # noqa: ANN202
        """打开（追加）日志文件并写入分隔头。"""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        path = self._log_dir / f"{name}.log"
        fh = path.open("ab")
        header = f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 =====\n"
        fh.write(header.encode("utf-8"))
        fh.flush()
        return fh, path

    def _spawn_win(self, spec: ServiceSpec) -> _Proc:
        """拉起 Windows 侧服务（ASR / 网关）。"""
        py = zjy13_python()
        if not py.is_file():
            py = Path(sys.executable)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        if spec.name == "asr":
            cmd = [
                str(py), "-m", "server.asr_service",
                "--host", "127.0.0.1",
                "--port", str(spec.port),
                "--preload",
            ]
            cwd = ZJY13_ROOT
        elif spec.name == "gateway":
            # 关键：把 TTS 指向 zjy14（:50001），ASR 指向独立服务（:50002）。
            # load_dotenv(override=False) 保证这些环境变量优先于 .env。
            env["TTS_BASE_URL"] = "http://127.0.0.1:50001"
            env["ASR_SERVICE_URL"] = "http://127.0.0.1:50002"
            cmd = [
                str(py), "-m", "server.ws.server",
                "--host", "0.0.0.0",
                "--port", str(spec.port),
                "--asr-url", "http://127.0.0.1:50002",
            ]
            cwd = ZJY13_ROOT
        else:  # pragma: no cover - 目前没有其它 win 服务
            raise ValueError(f"win 服务 {spec.name} 没有定义命令")

        fh, log_path = self._open_log(spec.name)
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        popen = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        return _Proc(popen=popen, log_path=log_path, started_at=time.time(),
                     port=spec.port)

    def _spawn_wsl(self, spec: ServiceSpec, *, engine: str) -> _Proc:
        """拉起 WSL 侧服务（TTS）。"""
        script = (
            f"cd /mnt/d/xiangmudaima/zjy14 && "
            f"export ZJY14_PORT={spec.port} && "
            f"exec bash scripts/start.sh {engine}"
        )
        cmd = ["wsl.exe", "-d", WSL_DISTRO, "-e", "bash", "-lc", script]
        fh, log_path = self._open_log(spec.name)
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        env = os.environ.copy()
        # wsl.exe 默认输出 UTF-16LE，会让日志文件里全是 \x00 间隔的乱码。
        # WSL_UTF8=1 让它改吐 UTF-8（WSL 0.64+ 支持）。
        env["WSL_UTF8"] = "1"
        popen = subprocess.Popen(  # noqa: S603
            cmd,
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        return _Proc(popen=popen, log_path=log_path, started_at=time.time(),
                     port=spec.port)

    def start(self, name: str, *, engine: str = "cosyvoice3") -> dict[str, Any]:
        """启动单个服务（不等待就绪；就绪由前端轮询状态）。"""
        spec = self.spec(name)

        if spec.kind == "probe":
            info = inspect_llm_env()
            return {
                "ok": bool(info.get("ok")),
                "name": name,
                "action": "probe",
                "message": info.get("detail") or "",
                "llm": info,
            }

        st = self.status_one(name)
        if st["healthy"]:
            return {
                "ok": True, "name": name, "action": "skip",
                "message": "已在运行且健康，无需重复启动",
            }

        if self._dry_run:
            return {
                "ok": True, "name": name, "action": "dry_run",
                "message": f"（dry-run）将启动 {spec.label}",
            }

        if spec.kind == "wsl":
            proc = self._spawn_wsl(spec, engine=engine)
        else:
            proc = self._spawn_win(spec)
        self._procs[name] = proc

        # 启动后短暂观察：进程秒退说明命令本身有问题（例如 WSL 拒绝访问、
        # 配置校验失败、venv 不存在）。此时立刻把日志尾巴回给调用方，
        # 而不是让用户等 ready_timeout 之后才发现失败。
        fast_fail = self._detect_fast_fail(spec, proc)
        if fast_fail:
            self._procs.pop(name, None)
            return {
                "ok": False,
                "name": name,
                "action": "failed",
                "message": fast_fail,
                "log_path": str(proc.log_path),
                "log_tail": _tail(proc.log_path, 30),
            }

        return {
            "ok": True,
            "name": name,
            "action": "started",
            "pid": proc.popen.pid if proc.popen else None,
            "message": f"{spec.label} 已启动，正在等待就绪",
            "log_path": str(proc.log_path),
        }

    def _detect_fast_fail(self, spec: ServiceSpec, proc: _Proc) -> str | None:
        """启动后 6 秒内退出且端口未监听 → 返回可读的失败原因。

        针对 WSL 单独给提示：``wsl.exe`` 在部分受限环境（被沙箱/作业对象
        限制的非交互进程）里会返回 ``Wsl/E_ACCESSDENIED``，
        这不是配置问题，需要用户在自己的终端里手动启动一次。
        """
        if proc.popen is None:
            return None
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if proc.popen.poll() is not None:
                break
            if port_listening(spec.port or 0):
                return None
            time.sleep(0.3)
        if proc.popen.poll() is None:
            return None  # 还活着，正常进入「等待就绪」

        tail = _tail(proc.log_path, 20)
        if spec.kind == "wsl" and (
            "ACCESSDENIED" in tail or "AccessDenied" in tail or "拒绝访问" in tail
        ):
            return (
                "WSL 拒绝访问（Wsl/E_ACCESSDENIED）：当前进程无法启动 WSL 发行版。"
                "请在你自己的 PowerShell 窗口里执行一次 "
                "`wsl -d " + WSL_DISTRO
                + " -e bash /mnt/d/xiangmudaima/zjy14/scripts/start.sh cosyvoice3`，"
                "再回到本控制台刷新状态即可接管。"
            )
        code = proc.popen.returncode
        return f"启动后立即退出（退出码 {code}）。日志末尾：\n{tail or '（日志为空）'}"

    def wait_healthy(self, name: str, timeout_s: float | None = None) -> bool:
        """阻塞等待某个服务健康（供 start_all 串行编排用）。"""
        spec = self.spec(name)
        limit = timeout_s if timeout_s is not None else spec.ready_timeout_s
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            if self.status_one(name)["healthy"]:
                return True
            # 进程提前退出就没必要再等
            proc = self._procs.get(name)
            if proc is not None and proc.popen is not None:
                if proc.popen.poll() is not None:
                    return False
            time.sleep(1.0)
        return False

    def start_all(self, *, engine: str = "cosyvoice3") -> dict[str, Any]:
        """一键启动全部服务（按依赖顺序，串行等待就绪）。

        编排规则（2026-11-26 修正）：
          - **不设全局失败短路**：原实现一旦某服务失败就把后面全部标
            「skipped」，连带把无副作用的 LLM 探测也跳过了。
            现在改为**按依赖判定**：只有真正依赖失败服务的才会被 block。
          - ``required=False`` 的服务（LLM 探测）永远不参与 ``failed``，
            它失败不该让整条链路判不成立。
        """
        results: list[dict[str, Any]] = []
        failed_required: list[str] = []

        for spec in self._specs:
            # 依赖检查：只看**必需**依赖是否健康
            missing = [
                d for d in spec.depends_on
                if not self.status_one(d)["healthy"]
            ]
            if missing:
                results.append({
                    "ok": False,
                    "name": spec.name,
                    "action": "blocked",
                    "message": f"依赖未就绪：{', '.join(missing)}",
                })
                if spec.required:
                    failed_required.append(spec.name)
                continue

            res = self.start(spec.name, engine=engine)
            results.append(res)
            if not res.get("ok"):
                if spec.required:
                    failed_required.append(spec.name)
                continue

            if res.get("action") == "started":
                ok = self.wait_healthy(spec.name)
                if not ok:
                    res["ok"] = False
                    res["message"] = (
                        f"{spec.label} 在 {spec.ready_timeout_s:.0f}s 内未就绪，见日志"
                    )
                    if spec.required:
                        failed_required.append(spec.name)

        return {
            "ok": not failed_required,
            "failed": failed_required[0] if failed_required else None,
            "failed_required": failed_required,
            "results": results,
            "status": self.status_all(),
        }

    # ---- 停止 ----

    def _wait_port_released(self, port: int | None, timeout: float = 4.0) -> bool:
        """等端口真正释放（taskkill 之后端口收回有延迟，立刻判定会误报失败）。"""
        if not port:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not port_listening(port):
                return True
            time.sleep(0.3)
        return not port_listening(port)

    def stop(self, name: str) -> dict[str, Any]:
        spec = self.spec(name)
        if spec.kind == "probe":
            return {
                "ok": True, "name": name, "action": "probe",
                "message": "LLM 是云端 API，没有本地进程可停止",
            }

        proc = self._procs.pop(name, None)
        messages: list[str] = []

        # 1) 终止本启动器拉起的进程树
        if proc is not None and proc.popen is not None:
            pid = proc.popen.pid
            if proc.popen.poll() is None:
                self._kill_tree(pid)
                messages.append(f"已终止进程树 PID={pid}")
            else:
                messages.append(f"进程已退出（退出码 {proc.popen.returncode}）")
            try:
                if proc.popen.stdout is not None:
                    proc.popen.stdout.close()
            except Exception:  # noqa: BLE001
                pass

        # 2) 等端口释放（taskkill 有延迟，立刻检查会误判失败）
        if spec.port:
            self._wait_port_released(spec.port, timeout=2.0)

        # 3) 非 WSL 服务：端口兜底（用户手动起的 / 启动器重启过）
        #
        #    注意：这里要跳过 WSL 转发进程与系统进程。
        #    对一个 Windows 服务来说，端口占用者本来就该是它自己；
        #    若实际是 ``wslrelay`` 等，说明「端口号撞车」或判断失误，
        #    杀它只会造成假成功 + 破坏 WSL 转发。
        if (
            spec.kind != "wsl"
            and spec.port
            and port_listening(spec.port)
        ):
            killed_pids: list[int] = []
            skipped: list[str] = []
            for pid, pname in self._port_holders(spec.port):
                if pname in NEVER_KILL_PROCESS_NAMES:
                    skipped.append(f"{pname}({pid})")
                    continue
                self._kill_tree(pid)
                killed_pids.append(pid)
            if killed_pids:
                messages.append(f"已终止占用端口 {spec.port} 的进程 {killed_pids}")
            if skipped:
                messages.append(
                    f"跳过系统/WSL 转发进程 {', '.join(skipped)}（杀它停不掉服务）"
                )
            self._wait_port_released(spec.port, timeout=3.0)

        # 4) WSL 服务：**只能**从 WSL 内部停。
        #
        #    为什么不能走上面的「按端口杀」（2026-11-27 事故）：
        #    Windows 侧 :50001 的监听者是 ``wslrelay``，不是 uvicorn。
        #    杀掉它 uvicorn 仍在跑（继续占显存），端口还会被 WSL 重新转发出来，
        #    结果就是「界面说停了，实际没停」。
        #    所以这里**要么** pkill 成功，**要么**如实报告没停掉。
        if spec.kind == "wsl":
            killed = self._pkill_wsl()
            if killed:
                messages.append("已停止 WSL 内的 TTS 进程")
                self._wait_port_released(spec.port, timeout=4.0)
            else:
                holder = self._describe_port_holder(spec.port)
                messages.append(
                    "TTS 仍在运行：本进程无法进入 WSL（wsl.exe 被系统拒绝）"
                    + (f"；端口 {spec.port} 由 {holder} 转发，"
                       "杀掉它既停不掉 TTS 也会破坏 WSL 转发，已跳过" if holder else "")
                )
                messages.append(
                    "请在你自己的 PowerShell 窗口执行以下任一命令："
                    f'  wsl -d {WSL_DISTRO} -e bash -lc "pkill -f custom_voice.server"'
                    "  或  .\\scripts\\stop_tts.ps1"
                )
                # 关键：pkill 失败就**不能**因为端口恰好没了而报成功
                return {
                    "ok": False,
                    "name": name,
                    "action": "stop_failed",
                    "message": "；".join(messages),
                    "port_still_listening": port_listening(spec.port or 0),
                    "needs_manual": True,
                    "manual_command": (
                        f'wsl -d {WSL_DISTRO} -e bash -lc '
                        f'"pkill -f custom_voice.server"'
                    ),
                }

        still = port_listening(spec.port or 0)
        return {
            "ok": not still,
            "name": name,
            "action": "stopped",
            "message": "；".join(messages) or "该服务未在运行",
            "port_still_listening": still,
        }

    def stop_all(self) -> dict[str, Any]:
        """倒序停止（先网关，再 ASR，最后 TTS）。"""
        results = [
            self.stop(spec.name) for spec in reversed(self._specs)
        ]
        return {
            "ok": all(r["ok"] for r in results),
            "results": results,
            "status": self.status_all(),
        }

    # ---- 进程工具 ----

    def _kill_tree(self, pid: int) -> None:
        """终止进程及其子进程。"""
        if sys.platform == "win32":
            subprocess.run(  # noqa: S603,S607
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0,
            )
        else:  # pragma: no cover
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)  # noqa: S603,S607

    def _pids_on_port(self, port: int) -> list[int]:
        """找占用指定 TCP 端口的进程 PID（只用 netstat，不需要管理员）。"""
        if sys.platform != "win32":  # pragma: no cover
            return []
        try:
            out = subprocess.run(  # noqa: S603,S607
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout
        except OSError:
            return []
        pids: list[int] = []
        needle = f":{port}"
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 5 or "LISTENING" not in line:
                continue
            if not parts[1].endswith(needle):
                continue
            try:
                pid = int(parts[-1])
            except ValueError:
                continue
            if pid and pid not in pids:
                pids.append(pid)
        return pids

    def _process_name(self, pid: int) -> str:
        """取进程名（小写、去掉 .exe）；查不到返回空串。

        ## 为什么不只用 tasklist（2026-11-27 实测）

        在受限上下文里（沙箱/作业对象，或与服务不同会话），`tasklist` 可能
        **完全看不到目标进程** —— 连 `/FO CSV` 的全量列表里都没有那一行，
        只回一句「没有运行的任务匹配指定标准」。此时 `wslrelay` 明明在监听，
        却查不到名字，后面的「是否系统进程」判断就失效了。

        所以先用 `ctypes` 按 PID 直接查（`OpenProcess` +
        `QueryFullProcessImageNameW`）：只依赖标准库，不受进程枚举可见性影响。
        tasklist 退为兜底（大多数正常环境里它更省事）。
        """
        if sys.platform != "win32":  # pragma: no cover
            return ""
        name = self._process_name_ctypes(pid)
        if name:
            return name
        return self._process_name_tasklist(pid)

    @staticmethod
    def _process_name_ctypes(pid: int) -> str:
        """按 PID 取进程名（Windows API，无需枚举全部进程）。"""
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
            ]
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE, wintypes.DWORD,
                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
            ]
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buf))
                if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)
                ):
                    return ""
                # 只取文件名，去掉 .exe
                return Path(buf.value).stem.lower()
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001  # pragma: no cover
            return ""

    @staticmethod
    def _process_name_tasklist(pid: int) -> str:
        """兜底：用 tasklist 查进程名。"""
        try:
            out = subprocess.run(  # noqa: S603,S607
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout
        except OSError:  # pragma: no cover
            return ""
        # 形如: "wslrelay.exe","11180","Console","1","12,345 K"
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith('"'):
                continue
            return line.split('"')[1].lower().removesuffix(".exe")
        return ""

    def _port_holders(self, port: int) -> list[tuple[int, str]]:
        """端口占用者列表 [(pid, 进程名小写)]。"""
        return [(pid, self._process_name(pid)) for pid in self._pids_on_port(port)]

    def _describe_port_holder(self, port: int | None) -> str:
        """人类可读的端口占用说明，用于让用户明白「为什么杀不掉」。

        例：``wslrelay(11180)`` —— 一看就知道是 WSL 的转发进程。
        """
        if not port:
            return ""
        holders = self._port_holders(port)
        if not holders:
            return ""
        return ", ".join(f"{n or '?'}({p})" for p, n in holders)

    def _pkill_wsl(self) -> bool:
        """在 WSL 内结束 zjy14 的 uvicorn 进程。"""
        try:
            res = subprocess.run(  # noqa: S603,S607
                [
                    "wsl.exe", "-d", WSL_DISTRO, "-e", "bash", "-lc",
                    "pkill -f 'custom_voice.server' && echo KILLED || echo NONE",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return "KILLED" in (res.stdout or "")

    # ---- 日志 ----

    def read_log(self, name: str, lines: int = 60) -> str:
        self.spec(name)  # 校验名字
        proc = self._procs.get(name)
        path = proc.log_path if proc else (self._log_dir / f"{name}.log")
        return _tail(path, lines)


__all__ = [
    "Orchestrator",
    "SERVICES",
    "SPEC_BY_NAME",
    "ServiceSpec",
    "WSL_DISTRO",
    "ZJY13_ROOT",
    "ZJY14_ROOT",
    "inspect_llm_env",
    "port_listening",
    "probe_health",
    "zjy13_python",
]