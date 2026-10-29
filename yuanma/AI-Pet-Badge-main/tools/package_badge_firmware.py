"""Export one checked, local-only fixed-screen firmware bundle; never flash it."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
NAMES={"qinglan":"青岚","elysia":"爱莉希雅"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--character",choices=tuple(NAMES),required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    build=ROOT/"firmware/ai_pet_badge/build"
    if f"BADGE_CHARACTER:STRING={args.character}" not in (build/"CMakeCache.txt").read_text(encoding="utf-8"):
        raise ValueError("CMake character does not match requested bundle")
    marker=f'BADGE_STATUS {{"character":"{args.character}","imu_mode":"shake_only"'.encode()
    if marker not in (build/"ai_pet_badge.bin").read_bytes():
        raise ValueError("Application image does not contain the requested fixed-screen profile")
    flash=json.loads((build/"flasher_args.json").read_text(encoding="utf-8"))
    sources=[]
    for name in list(flash["flash_files"].values())+["flash_args","flasher_args.json"]:
        source=(build/name).resolve()
        if not source.is_relative_to(build.resolve()) or not source.is_file():
            raise ValueError("Invalid firmware source path")
        sources.append((name,source))
    if args.output.exists():
        raise ValueError("Output already exists; choose a new directory")
    args.output.mkdir(parents=True)
    hashes={}
    for name,source in sources:
        target=args.output/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
        hashes[name]=digest(target)
    script=args.output/"Flash-Badge.ps1"
    script.write_text((ROOT/"tools/Flash-Badge.ps1").read_text(encoding="utf-8"),encoding="utf-8-sig")
    hashes[script.name]=digest(script)
    base_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    dirty=bool(subprocess.check_output(["git","status","--porcelain"],cwd=ROOT,text=True).strip())
    manifest={"character":args.character,"name":NAMES[args.character],"mode":"shake_only",
              "orientation_enabled":False,"gyro_enabled":False,"export_action":"files_only",
              "idle":"standing" if args.character=="qinglan" else "seated",
              "idf":"5.5.3","file_sha256":hashes,"flash_files":flash["flash_files"],
              "source_base_commit":base_commit,"source_commit":None if dirty else base_commit,"source_dirty":dirty,
              "source_sha256":{p.name:digest(p) for p in sorted((ROOT/"firmware/ai_pet_badge/main").glob("*"))
                               if p.suffix in (".c",".h") and p.name!="wifi_config.h"}}
    (args.output/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Exported {args.character}: {args.output} ({(args.output/'ai_pet_badge.bin').stat().st_size:,} application bytes)")


if __name__=="__main__":
    main()
