#!/usr/bin/env bash
set -uo pipefail
source "${HOME}/cosyvoice-venv/bin/activate"
python - <<'PY'
import importlib
for name in ("soundfile", "miniaudio", "scipy", "librosa"):
    try:
        m = importlib.import_module(name)
        print(f"{name:12} OK   {getattr(m, '__version__', '?')}")
    except Exception as exc:
        print(f"{name:12} MISSING")
PY
echo "--- 读取参考音频 ---"
python - <<'PY'
import soundfile as sf
p = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav"
info = sf.info(p)
print("sr", info.samplerate, "ch", info.channels, "dur", round(info.duration, 3),
      "fmt", info.format, info.subtype)
PY
