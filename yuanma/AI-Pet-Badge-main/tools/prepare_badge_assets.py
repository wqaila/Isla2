"""Convert the supplied green-screen videos into a validated ESP32 frame pack."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import zlib
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

MAGIC = b"BDGZ565\0"
HEADER = struct.Struct("<8s10I")
CLIP = struct.Struct("<16sII")
FRAME = struct.Struct("<II")
SIZE = 360
DECODE_SIZE = 540
BACKGROUND = (255, 240, 245)
NAMES = ("walk", "jump", "sit")
STANDING_HEIGHT = 190
GROUND_Y = 310
SIT_START = 1.0
SIT_END = 3.1
SIT_SPEED = 1.4
BLEND_SECONDS = 0.25
ROI = (104, 80, 160, 256)


def decode(path: Path, fps: int, *, sitting: bool = False) -> list[np.ndarray]:
    filters = f"fps={fps},scale={DECODE_SIZE}:{DECODE_SIZE}"
    if sitting:
        filters = (f"trim=start={SIT_START}:end={SIT_END},"
                   f"setpts=(PTS-STARTPTS)/{SIT_SPEED}," + filters)
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(path),
               "-an", "-vf", filters,
               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    result = subprocess.run(command, capture_output=True, check=True)
    frame_size = DECODE_SIZE * DECODE_SIZE * 3
    if not result.stdout or len(result.stdout) % frame_size:
        raise ValueError(f"Incomplete video frames: {path}")
    data = np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, DECODE_SIZE, DECODE_SIZE, 3)
    return list(data)


def matte(rgb: np.ndarray) -> np.ndarray:
    signed = rgb.astype(np.int16)
    green_excess = signed[:, :, 1] - np.maximum(signed[:, :, 0], signed[:, :, 2])
    return (255 * (1 - np.clip((green_excess - 12) / 65, 0, 1))).astype(np.uint8)


def encode565(image: Image.Image) -> bytes:
    pixels = np.asarray(image.convert("RGB"), dtype=np.uint16)
    packed = ((pixels[:, :, 0] >> 3) << 11) | ((pixels[:, :, 1] >> 2) << 5) | (pixels[:, :, 2] >> 3)
    return packed.astype("<u2").tobytes()


def dashboard_background() -> Image.Image:
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    vertical = y / (SIZE - 1)
    glow = np.clip(1 - np.sqrt(((x - 195) / 260) ** 2 + ((y - 145) / 270) ** 2), 0, 1)
    top = np.array([255, 236, 246])
    bottom = np.array([236, 236, 255])
    rgb = top[None, None, :] * (1 - vertical[:, :, None]) + bottom[None, None, :] * vertical[:, :, None]
    rgb = np.clip(rgb + glow[:, :, None] * 7, 0, 255).astype('uint8')
    canvas = Image.fromarray(rgb)
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((7, 7, 352, 352), outline=(230, 204, 229), width=1)
    draw.ellipse((14, 14, 345, 345), outline=(255, 249, 255), width=1)
    draw.ellipse((111, 317, 263, 337), fill=(222, 217, 241))
    draw.ellipse((118, 317, 256, 332), fill=(234, 226, 245))
    for px, py, radius in ((72, 112, 6), (291, 109, 5), (68, 302, 4), (283, 287, 6)):
        draw.polygon([(px, py - radius), (px + 2, py - 2), (px + radius, py),
                      (px + 2, py + 2), (px, py + radius), (px - 2, py + 2),
                      (px - radius, py), (px - 2, py - 2)], fill=(255, 253, 255))
    # Fixed cards are baked once, so LVGL only draws the live text/icons while
    # animating instead of repeatedly blending shadows and rounded masks.
    boxes = ((28, 151, 110, 277), (262, 174, 339, 250))
    shadow = Image.new('RGBA', canvas.size)
    shadow_draw = ImageDraw.Draw(shadow)
    for left, top, right, bottom in boxes:
        shadow_draw.rounded_rectangle((left, top + 3, right, bottom + 3), radius=18,
                                      fill=(160, 136, 185, 35))
    result = Image.alpha_composite(canvas.convert('RGBA'), shadow.filter(ImageFilter.GaussianBlur(5)))
    cards = Image.new('RGBA', canvas.size)
    card_draw = ImageDraw.Draw(cards)
    for box in boxes:
        card_draw.rounded_rectangle(box, radius=18, fill=(255, 255, 255, 160),
                                    outline=(255, 255, 255, 210), width=1)
    return Image.alpha_composite(result, cards).convert('RGB')


def character_bounds(rgb: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(matte(rgb) > 127)
    if not xs.size:
        raise ValueError("Video contains an empty character frame")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def transition_preview(clips: dict[str, list[Image.Image]], name: str, fps: int) -> list[Image.Image]:
    """Mirror the firmware's sit -> stand -> action -> sit sequence."""
    sitting = clips["sit"]
    result = [sitting[-1]] * (fps // 2) + list(reversed(sitting[:-1]))
    if name == "sit":
        result += [sitting[0]] * round(BLEND_SECONDS * fps)
    else:
        def blend(source: Image.Image, target: Image.Image) -> list[Image.Image]:
            count = max(2, round(BLEND_SECONDS * fps))
            return [Image.blend(source, target, (t := i / count) ** 2 * (3 - 2 * t))
                    for i in range(1, count + 1)]
        result += blend(sitting[0], clips[name][0])
        result += clips[name][1:]
        result += blend(clips[name][-1], sitting[0])
    return result + sitting[1:] + [sitting[-1]] * fps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preview-dir", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=24, choices=range(5, 31))
    args = parser.parse_args()
    clips = {name: decode(args.source / f"{name}.mp4", args.fps, sitting=name == "sit")
             for name in NAMES}
    args.preview_dir.mkdir(parents=True, exist_ok=True)
    compressed: list[bytes] = []
    descriptors = []
    preview_tiles = []
    preview_clips = {}
    transforms = {}
    background = dashboard_background()
    background.save(args.preview_dir / 'background.png')
    for name, frames in clips.items():
        # A constant transform per clip aligns the standing poses without cancelling
        # the jump or moving the character's feet on every frame.
        reference = character_bounds(frames[0])
        all_bounds = np.asarray([character_bounds(rgb) for rgb in frames])
        bounds = (int(all_bounds[:, 0].min()), int(all_bounds[:, 1].min()),
                  int(all_bounds[:, 2].max()), int(all_bounds[:, 3].max()))
        scale = STANDING_HEIGHT / (reference[3] - reference[1])
        shape = (round((bounds[2] - bounds[0]) * scale), round((bounds[3] - bounds[1]) * scale))
        position = (round(SIZE / 2 + (bounds[0] - (reference[0] + reference[2]) / 2) * scale),
                    round(GROUND_Y + (bounds[1] - reference[3]) * scale))
        if min(position) < 0 or position[0] + shape[0] > SIZE or position[1] + shape[1] > SIZE:
            raise ValueError(f"Normalized character would be clipped: {name}")
        transforms[name] = {"source_bounds": bounds, "standing_bounds": reference,
                            "size": shape, "position": position, "scale": scale}
        descriptors.append((name, len(compressed), len(frames)))
        previews = []
        for rgb in frames:
            alpha = matte(rgb)
            clean = rgb.copy()
            fringe = (alpha > 0) & (alpha < 255)
            clean[:, :, 1][fringe] = np.minimum(clean[:, :, 1][fringe],
                                               np.maximum(clean[:, :, 0][fringe], clean[:, :, 2][fringe]))
            foreground = Image.fromarray(np.dstack((clean, alpha))).crop(tuple(bounds))
            foreground = foreground.resize(shape, Image.Resampling.LANCZOS)
            layer = Image.new('RGBA', (SIZE, SIZE))
            layer.paste(foreground, position)
            rx, ry, rw, rh = ROI
            box = layer.getbbox()
            if box and (box[0] < rx or box[1] < ry or box[2] > rx + rw or box[3] > ry + rh):
                raise ValueError(f"Character exceeds sprite ROI: {name}, {box}")
            sprite = layer.crop((rx, ry, rx + rw, ry + rh))
            rgba = np.asarray(sprite).copy()
            rgba[rgba[:, :, 3] == 0, :3] = 0
            raw = encode565(Image.fromarray(rgba[:, :, :3])) + rgba[:, :, 3].tobytes()
            canvas = Image.alpha_composite(background.convert('RGBA'), layer).convert('RGB')
            block = zlib.compress(raw, level=9)
            if zlib.decompress(block) != raw:
                raise ValueError("Frame compression failed round-trip verification")
            compressed.append(block)
            previews.append(canvas)
        previews[0].save(args.preview_dir / f"{name}.png")
        previews[0].save(args.preview_dir / f"{name}.gif", save_all=True, append_images=previews[1:],
                         duration=round(1000 / args.fps), loop=0)
        preview_clips[name] = previews
        tile = previews[len(previews) // 2].copy()
        ImageDraw.Draw(tile).text((130, 330), name, fill=(100, 54, 86))
        preview_tiles.append(tile)
    offset = HEADER.size + len(descriptors) * CLIP.size + len(compressed) * FRAME.size
    backdrop = zlib.compress(encode565(background), level=9)
    background_offset = offset
    offset += len(backdrop)
    pack = bytearray(HEADER.pack(MAGIC, 2, ROI[2], ROI[3], args.fps,
                                len(descriptors), len(compressed), ROI[0], ROI[1],
                                background_offset, len(backdrop)))
    for name, first, count in descriptors:
        pack.extend(CLIP.pack(name.encode("ascii"), first, count))
    for block in compressed:
        pack.extend(FRAME.pack(offset, len(block)))
        offset += len(block)
    pack.extend(backdrop)
    for block in compressed:
        pack.extend(block)
    if len(pack) != offset:
        raise ValueError("Frame pack offsets do not match file size")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(pack)
    sheet = Image.new("RGB", (SIZE * len(preview_tiles), SIZE), BACKGROUND)
    for index, tile in enumerate(preview_tiles):
        sheet.paste(tile, (index * SIZE, 0))
    sheet.save(args.preview_dir / "contact-sheet.png")
    for name in NAMES:
        timeline = transition_preview(preview_clips, name, args.fps)
        timeline[0].save(args.preview_dir / f"sequence-{name}.webp", save_all=True,
                         append_images=timeline[1:], duration=round(1000 / args.fps), loop=0,
                         lossless=True)
    joins = [preview_clips["sit"][-1], preview_clips["sit"][0],
             preview_clips["walk"][0], preview_clips["jump"][0]]
    sheet = Image.new("RGB", (SIZE * len(joins), SIZE), BACKGROUND)
    for index, tile in enumerate(joins):
        sheet.paste(tile, (index * SIZE, 0))
    sheet.save(args.preview_dir / "aligned-poses.png")
    manifest = {"format": "BDGZ565-v2", "size": [SIZE, SIZE], "sprite_roi": ROI,
                "pixel_format": "RGB565_LE+A8 planes", "fps": args.fps,
                "frames": len(compressed), "bytes": len(pack), "sha256": hashlib.sha256(pack).hexdigest(),
                "standing_height": STANDING_HEIGHT, "ground_y": GROUND_Y,
                "transforms": transforms,
                "sit_source_seconds": [SIT_START, SIT_END], "sit_speed": SIT_SPEED,
                "clips": [{"name": name, "first_frame": first, "frame_count": count}
                          for name, first, count in descriptors]}
    args.output.with_suffix(".json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
