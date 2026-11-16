import wave, pathlib
d = pathlib.Path("/mnt/d/xiangmudaima/zjy14/_audio_probe")
for p in sorted(d.glob("*.wav")):
    with wave.open(str(p)) as w:
        print(f"{p.name:<20} {w.getframerate():>6}Hz {w.getnchannels()}ch "
              f"{w.getsampwidth()*8}bit {w.getnframes()/w.getframerate():.2f}s")
