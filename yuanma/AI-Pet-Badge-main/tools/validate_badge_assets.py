"""Validate the complete v2 embedded pack without the original videos."""
import argparse
from pathlib import Path
import struct
import zlib

from prepare_badge_assets import HEADER, CLIP, FRAME, MAGIC, ROI, SIZE


def validate(path: Path, character: str = 'elysia'):
    data = path.read_bytes()
    magic, version, width, height, fps, clip_count, frame_count, x, y, bg, bg_length = HEADER.unpack_from(data)
    assert magic == MAGIC and version == 2, 'Rebuild assets with the v2 converter'
    assert (x, y, width, height) == ROI and 5 <= fps <= 30
    assert clip_count == 3 and 0 < frame_count <= 4096
    table = HEADER.size + clip_count * CLIP.size
    assert bg == table + frame_count * FRAME.size
    assert len(zlib.decompress(data[bg:bg+bg_length])) == SIZE*SIZE*2
    names, coverage = [], []
    for i in range(clip_count):
        name, first, count = CLIP.unpack_from(data, HEADER.size + i*CLIP.size)
        names.append(name.rstrip(b'\0').decode('ascii'))
        assert count >= 2 and first+count <= frame_count
        coverage.extend(range(first, first+count))
    expected = ['walk', 'jump', 'wave' if character == 'qinglan' else 'sit']
    assert names == expected, f'Expected {character} clips {expected}, got {names}'
    assert sorted(coverage) == list(range(frame_count))
    end = bg+bg_length
    for i in range(frame_count):
        offset, length = FRAME.unpack_from(data, table+i*FRAME.size)
        assert offset == end and length > 0 and offset+length <= len(data)
        raw = zlib.decompress(data[offset:offset+length])
        assert len(raw) == width*height*3
        alpha = raw[2*width*height:]
        assert any(alpha) and 0 in alpha and 255 in alpha
        assert len(raw[:2*width*height]) == width*height*2
        for p, a in enumerate(alpha):
            if not a:
                assert raw[2*p:2*p+2] == b'\0\0', 'Transparent RGB must be zero'
        end = offset+length
    assert end == len(data), 'Unexpected trailing data'
    if character == 'qinglan':
        # Wave anchors also supply the static standing and dizzy neutral pose.
        _, first, count = CLIP.unpack_from(data, HEADER.size+2*CLIP.size)
        anchors = []
        for i in (first, first+count-1):
            offset, length = FRAME.unpack_from(data, table+i*FRAME.size)
            anchors.append(zlib.decompress(data[offset:offset+length]))
        assert anchors[0] == anchors[1], 'Standing anchors must match exactly'
    print(f'PASS v2: {frame_count} frames, {len(data):,} bytes, {fps} FPS, RGB565+A8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pack', type=Path)
    parser.add_argument('--character', choices=('elysia','qinglan'), default='elysia')
    args = parser.parse_args()
    validate(args.pack, args.character)
