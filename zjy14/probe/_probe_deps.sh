#!/usr/bin/env bash
set -uo pipefail
source "${HOME}/cosyvoice-venv/bin/activate"
python - <<'PY'
import sys
print("python", sys.version.split()[0])
for name in ("pytest", "fastapi", "pydantic", "uvicorn", "yaml", "soundfile", "numpy", "httpx"):
    try:
        m = __import__(name)
        ver = getattr(m, "__version__", getattr(m, "VERSION", "?"))
        print(f"{name:12} OK  {ver}")
    except Exception as exc:
        print(f"{name:12} MISSING  {type(exc).__name__}: {exc}")
PY
