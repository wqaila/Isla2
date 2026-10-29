"""Relocate local ESP-IDF component paths while preserving locked versions."""
from pathlib import Path

import yaml

project = Path(__file__).resolve().parents[1] / "firmware" / "ai_pet_badge"
path = project / "dependencies.lock"
if path.exists():
    lock = yaml.safe_load(path.read_text(encoding="utf-8"))
    for name, entry in lock["dependencies"].items():
        if entry["source"]["type"] == "local":
            component = project / "components" / name.replace("/", "__")
            if not component.is_dir():
                raise SystemExit(f"Missing local component: {component}")
            entry["source"]["path"] = component.as_posix()
    path.write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")
    print("Local component paths point to the current checkout.")
