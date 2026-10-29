"""Combine supplied voice clips into a local 24kHz mono PCM reference; no upload."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import wave

import imageio_ffmpeg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if any(not path.is_file() for path in args.inputs):
        parser.error("One or more input files do not exist")
    if args.output.exists():
        parser.error("Output exists; choose a new filename")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-n"]
    for path in args.inputs:
        command.extend(["-i", str(path)])
    filters = [f"[{i}:a:0]aresample=24000,aformat=sample_fmts=s16:channel_layouts=mono[a{i}]"
               for i in range(len(args.inputs))]
    filters.append("".join(f"[a{i}]" for i in range(len(args.inputs))) +
                   f"concat=n={len(args.inputs)}:v=0:a=1[out]")
    command.extend(["-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", "pcm_s16le", str(args.output)])
    subprocess.run(command, check=True)
    with wave.open(str(args.output), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
        metadata = {"inputs": [str(p.resolve()) for p in args.inputs], "duration": duration,
                    "sample_rate": audio.getframerate(), "channels": audio.getnchannels(),
                    "sample_width": audio.getsampwidth(), "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}
    args.output.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {args.output.resolve()} ({duration:.2f}s, 24kHz mono 16-bit PCM)")
    if not 10 <= duration <= 20:
        print("Recommended reference duration is 10-20s; choose clips accordingly.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
