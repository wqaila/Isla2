"""Import an owned Pets v2 RGBA atlas into Qinglan's ESP32 animation pack.

This is a pixel-format conversion, not a new pet upload. Source artwork stays local.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from prepare_badge_assets import HEADER, CLIP, FRAME, MAGIC, ROI, SIZE, dashboard_background, encode565

CELL = (192, 208)
ATLAS = (1536, 2288)
FPS = 24
ROWS = {"walk": (1, [120]*7+[220]), "jump": (4, [140]*4+[280]),
        "wave": (3, [140]*3+[280])}


def extract(sheet: Image.Image, row: int, column: int) -> Image.Image:
    w, h = CELL
    frame = sheet.crop((column*w, row*h, (column+1)*w, (row+1)*h))
    if frame.getchannel("A").getbbox() is None:
        raise ValueError(f"Empty source cell: row {row}, column {column}")
    return frame


def resample(frames: list[Image.Image], durations: list[int]) -> list[Image.Image]:
    """Sample cumulative source durations, retaining the final pose's longer hold."""
    ends = np.cumsum(durations)
    count = max(2, round(int(ends[-1])*FPS/1000))
    return [frames[min(int(np.searchsorted(ends, (i+0.5)*1000/FPS)), len(frames)-1)]
            for i in range(count)]


def place(frame: Image.Image, scale: float, origin: tuple[int, int]) -> Image.Image:
    size = (round(CELL[0]*scale), round(CELL[1]*scale))
    # Resize premultiplied pixels so transparent dark RGB cannot fringe the robe.
    foreground = frame.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")
    layer = Image.new("RGBA", (SIZE, SIZE))
    layer.paste(foreground, origin)
    x, y, w, h = ROI
    bounds = layer.getchannel("A").getbbox()
    if not bounds or bounds[0] < x+4 or bounds[1] < y+4 or bounds[2] > x+w-4 or bounds[3] > y+h-4:
        raise ValueError(f"Character exceeds safe board sprite bounds: {bounds}")
    return layer


def preview_hud(background: Image.Image) -> Image.Image:
    """Desktop approximation of the unchanged HUD; not a captured board image."""
    source = Path(__file__).resolve().parents[1]/"firmware/ai_pet_badge/managed_components/lvgl__lvgl/scripts/built_in_font"
    han = source/"SourceHanSansSC-Normal.otf"
    result = background.convert("RGBA")
    draw = ImageDraw.Draw(result)
    small = ImageFont.truetype(str(han), 14)
    name = ImageFont.truetype(str(han), 20)
    clock = ImageFont.truetype(str(source/"Montserrat-Medium.ttf"), 30)
    def text(x, y, value, font=small, color="#8A648D", center=False):
        if center:
            x -= draw.textlength(value, font=font)/2
        draw.text((x, y), value, font=font, fill=color, anchor="lt")
    text(99, 24, "12:00", clock, "#765C88")
    text(202, 29, "10月28日")
    text(202, 49, "周三")
    text(180, 66, "青岚", name, center=True)
    text(69, 160, "深圳", center=True)
    draw.ellipse((56,188,73,205), fill="#9F94CA")
    draw.ellipse((65,183,81,205), fill="#9F94CA")
    draw.rounded_rectangle((52,195,85,208), 6, fill="#9F94CA")
    text(69, 221, "--°C", name, "#81738E", True)
    text(69, 245, "待同步", center=True)
    text(300, 191, "陪伴中", center=True)
    text(300, 218, "轻触互动", center=True)
    draw.ellipse((297,164,303,170), fill="#7DB6A4")
    text(180, 337, "在你身边", center=True)
    return result


def composite(hud: Image.Image, layer: Image.Image) -> Image.Image:
    return Image.alpha_composite(hud, layer).convert("RGB")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preview-dir", type=Path, required=True)
    args = parser.parse_args()
    with Image.open(args.source) as original:
        if original.size != ATLAS or original.mode != "RGBA":
            raise ValueError(f"Expected a Pets v2 RGBA atlas {ATLAS}; got {original.size}, {original.mode}")
        sheet = original.copy()
    neutral = extract(sheet, 0, 0)
    groups = {name: [extract(sheet, row, i) for i in range(len(timing))]
              for name, (row, timing) in ROWS.items()}
    bounds = neutral.getchannel("A").getbbox()
    scale = 190/(bounds[3]-bounds[1])
    origin = (round(180-CELL[0]*scale/2), round(310-bounds[3]*scale))
    idle = place(neutral, scale, origin)
    layers = {}
    for name, source_frames in groups.items():
        sampled = resample(source_frames, ROWS[name][1])
        if name == "walk":
            sampled *= 2  # Two existing strides; no invented frames or direction changes.
        layers[name] = [place(f, scale, origin) for f in sampled]
    layers["wave"] = [idle]+layers["wave"]+[idle]

    background = dashboard_background()
    hud = preview_hud(background)
    args.preview_dir.mkdir(parents=True, exist_ok=True)
    composite(hud, idle).save(args.preview_dir/"idle.png")
    descriptors, blocks, clip_previews = [], [], {}
    for name, frames in layers.items():
        descriptors.append((name, len(blocks), len(frames)))
        previews = []
        for frame in frames:
            x, y, w, h = ROI
            pixels = np.asarray(frame.crop((x, y, x+w, y+h))).copy()
            pixels[pixels[:, :, 3] == 0, :3] = 0
            raw = encode565(Image.fromarray(pixels[:, :, :3]))+pixels[:, :, 3].tobytes()
            block = zlib.compress(raw, 9)
            if zlib.decompress(block) != raw:
                raise ValueError("Compression round-trip failed")
            blocks.append(block)
            previews.append(composite(hud, frame))
        clip_previews[name] = previews
        # Match firmware's 250 ms premultiplied-alpha transitions and static idle.
        timeline = [composite(hud, idle)]*12
        for start, end, action in ((idle, frames[0], True), (frames[-1], idle, False)):
            for i in range(1, 7):
                t = i/6; t = t*t*(3-2*t)
                blended = Image.blend(start.convert("RGBa"), end.convert("RGBa"), t).convert("RGBA")
                timeline.append(composite(hud, blended))
            if action:
                timeline += previews
        timeline += [composite(hud, idle)]*12
        timeline[0].save(args.preview_dir/f"{name}.webp", save_all=True,
                         append_images=timeline[1:], duration=round(1000/FPS), loop=0, lossless=True)

    offset = HEADER.size+len(descriptors)*CLIP.size+len(blocks)*FRAME.size
    backdrop = zlib.compress(encode565(background), 9)
    pack = bytearray(HEADER.pack(MAGIC, 2, ROI[2], ROI[3], FPS, len(descriptors),
                                len(blocks), ROI[0], ROI[1], offset, len(backdrop)))
    for name, first, count in descriptors:
        pack.extend(CLIP.pack(name.encode("ascii"), first, count))
    offset += len(backdrop)
    for block in blocks:
        pack.extend(FRAME.pack(offset, len(block)))
        offset += len(block)
    pack.extend(backdrop)
    for block in blocks:
        pack.extend(block)
    if len(pack) != offset:
        raise ValueError("Pack offsets are inconsistent")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(pack)
    manifest = {"format": "BDGZ565-v2", "character": "qinglan", "idle_pose": "standing",
                "fps": FPS, "frames": len(blocks), "bytes": len(pack), "sprite_roi": ROI,
                "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
                "sha256": hashlib.sha256(pack).hexdigest(), "source_size": ATLAS,
                "cell_size": CELL, "scale": scale, "origin": origin,
                "clips": [{"name": n, "first_frame": f, "frame_count": c} for n, f, c in descriptors],
                "previews": "Desktop HUD approximation with placeholder time/weather; not board captures"}
    args.output.with_suffix(".json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    contact = Image.new("RGB", (4*SIZE, SIZE+30), "#FFF0F5")
    for col, (name, preview) in enumerate([("idle", composite(hud, idle))]+
            [(n, p[len(p)//2]) for n,p in clip_previews.items()]):
        contact.paste(preview, (col*SIZE, 0))
        ImageDraw.Draw(contact).text((col*SIZE+10, SIZE+5), f"DESKTOP PREVIEW / {name}", fill="#765C88")
    contact.save(args.preview_dir/"contact-sheet.png")
    print(json.dumps(manifest, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
