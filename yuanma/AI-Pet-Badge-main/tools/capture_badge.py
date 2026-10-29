"""Capture the actual LVGL screen over USB without resetting the board."""
import argparse
import time
from pathlib import Path

import numpy as np
from PIL import Image
import serial

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port', required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--before', choices=('NEXT', 'IMU BENCH'), help='Run a bounded diagnostic before capturing')
parser.add_argument('--delay', type=float, default=0, help='Seconds to wait before SHOT')
args = parser.parse_args()
port = serial.Serial(port=None, baudrate=115200, timeout=0.2)
port.dtr = False
port.rts = False
port.port = args.port
try:
    port.open()
    port.reset_input_buffer()
    if args.before:
        port.write((args.before+'\n').encode('ascii'))
        port.flush()
    if args.delay > 0:
        time.sleep(args.delay)
        port.reset_input_buffer()
    port.write(b'SHOT\n')
    deadline = time.monotonic() + 20
    header = None
    while time.monotonic() < deadline:
        line = port.readline()
        if line.startswith(b'BADGE_SNAPSHOT '):
            header = line.split()
            break
    if header is None:
        raise RuntimeError('No snapshot response; verify firmware and USB port')
    width, height, stride, length = map(int, header[1:])
    if not (width == height == 360 and stride >= width * 2 and length == stride * height):
        raise RuntimeError(f'Unexpected snapshot dimensions: {header!r}')
    payload = bytearray()
    while len(payload) < length and time.monotonic() < deadline:
        payload.extend(port.read(min(4096, length - len(payload))))
    if len(payload) != length:
        raise RuntimeError(f'Incomplete screenshot: {len(payload)}/{length}')
finally:
    port.close()
pixels = np.frombuffer(payload, dtype='<u2').reshape(height, stride // 2)[:, :width].astype(np.uint32)
rgb = np.stack(((pixels >> 11) * 255 // 31, ((pixels >> 5) & 63) * 255 // 63,
                (pixels & 31) * 255 // 31), axis=-1).astype('uint8')
args.output.parent.mkdir(parents=True, exist_ok=True)
Image.fromarray(rgb).save(args.output)
print(args.output)
