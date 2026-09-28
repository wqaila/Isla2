"""Windows SAPI 适配器 —— 离线、零依赖的兜底引擎。

直接调系统自带的 ``System.Speech``（通过 PowerShell），
**不需要装任何 Python 包**，所以它永远可用。

定位是**兜底**：音色机械、没有情感，但「能用」这件事上最可靠。
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from ..base import AudioResult, TTSEngine

#: PowerShell 脚本：读文本文件 → 合成 WAV。
#: 文本通过**文件**传入而不是命令行参数，避免转义与注入问题。
_PS_SCRIPT = r"""
param([string]$TextFile, [string]$OutFile, [string]$Voice, [int]$Rate)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$t = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($Voice) { try { $s.SelectVoice($Voice) } catch { } }
    $s.Rate = $Rate
    $s.SetOutputToWaveFile($OutFile)
    $s.Speak($t)
} finally {
    $s.Dispose()
}
"""

#: 列出已安装音色
_PS_VOICES = r"""
Add-Type -AssemblyName System.Speech
(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() |
    ForEach-Object { $_.VoiceInfo.Name }
"""


def wav_duration_ms(path: Path) -> int:
    """从 WAV 头读时长，避免为此引入音频库。"""
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate() or 1
            return int(w.getnframes() / rate * 1000)
    except Exception:
        return 0


def _powershell() -> str | None:
    """找一个可用的 PowerShell 可执行文件。"""
    for exe in ("powershell.exe", "pwsh.exe"):
        found = shutil.which(exe)
        if found:
            return found
    return None


def _run_ps(script: str, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """把脚本写成临时 .ps1 再执行（避免命令行引号地狱）。"""
    ps = _powershell()
    if not ps:
        raise RuntimeError("找不到 PowerShell，SAPI 引擎不可用")

    with tempfile.TemporaryDirectory(prefix="tts_sapi_") as td:
        script_path = Path(td) / "run.ps1"
        script_path.write_text(script, encoding="utf-8")
        cmd = [ps, "-NoProfile", "-NonInteractive",
               "-ExecutionPolicy", "Bypass",
               "-File", str(script_path)] + args
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="ignore", timeout=timeout)


class SapiEngine(TTSEngine):
    """Windows 系统自带语音合成。"""

    name = "sapi"
    label = "Windows SAPI（离线兜底）"
    ext = "wav"
    mime = "audio/wav"
    online = False

    @classmethod
    def is_available(cls) -> bool:
        """Windows 且有 PowerShell 就算可用。

        这里不做「真出一段声」的探测 —— 那要几百毫秒且会写临时文件。
        真正的失败会在 :meth:`synthesize` 里暴露，由 Controller 降级处理。
        """
        return sys.platform == "win32" and _powershell() is not None

    @classmethod
    async def list_voices(cls) -> list[dict]:
        if not cls.is_available():
            return []
        try:
            r = await asyncio.to_thread(_run_ps, _PS_VOICES, [], 30)
            if r.returncode != 0:
                return []
            names = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
            return [{"id": n, "label": n} for n in names]
        except Exception:
            return []

    async def synthesize(self, text: str, voice: str = "", speed: float = 1.0,
                         out_path: Path | None = None) -> AudioResult:
        if not text.strip():
            raise ValueError("文本为空，无法合成")

        if out_path is None:
            fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="tts_sapi_")
            import os
            os.close(fd)
            out_path = Path(tmp)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # SAPI 的 Rate 是 -10..10，1.0 倍速对应 0
        rate = int(max(-10, min(10, round((float(speed) - 1.0) * 10))))

        def _work() -> None:
            with tempfile.TemporaryDirectory(prefix="tts_sapi_in_") as td:
                txt_path = Path(td) / "text.txt"
                txt_path.write_text(text, encoding="utf-8")
                r = _run_ps(_PS_SCRIPT,
                            ["-TextFile", str(txt_path),
                             "-OutFile", str(out_path),
                             "-Voice", voice or "",
                             "-Rate", str(rate)])
                if r.returncode != 0 or not out_path.exists():
                    detail = (r.stderr or r.stdout or "").strip()[:300]
                    raise RuntimeError(f"SAPI 合成失败（exit {r.returncode}）：{detail}")

        # PowerShell 是阻塞调用，扔到线程里别卡住事件循环
        await asyncio.to_thread(_work)

        return AudioResult(path=out_path, mime=self.mime, engine=self.name,
                           duration_ms=wav_duration_ms(out_path))
