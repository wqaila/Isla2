"""Read the USB console without holding BOOT low, optionally issuing diagnostics.

Run with the ESP-IDF Python environment (it supplies pyserial and esptool).
"""
import argparse
import sys
import time
from pathlib import Path

import serial
from esptool.reset import HardReset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--command", choices=("STATUS", "NEXT", "TONE", "DASH", "SD", "IMU", "IMU CALIBRATE", "IMU SELFTEST", "IMU BENCH"))
    parser.add_argument("--sync-time", action="store_true", help="Set the clock from this computer and update the board RTC")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    port = serial.Serial(port=None, baudrate=115200, timeout=0.2)
    port.dtr = False
    port.rts = False
    port.port = args.port
    chunks = []
    try:
        port.open()
        if args.reset:
            HardReset(port, uses_usb=True)()
            if args.command or args.sync_time:
                time.sleep(3)
        if args.sync_time:
            port.write(f"TIME {int(time.time())}\n".encode("ascii"))
            port.flush()
        if args.command:
            port.write((args.command + "\n").encode("ascii"))
            port.flush()
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            block = port.read(4096)
            if block:
                chunks.append(block)
    finally:
        port.close()
    text = b"".join(chunks).decode("utf-8", errors="replace").replace("\r\r\n", "\n").replace("\r\n", "\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(errors="replace")
    print(text if text else "No console output received.")


if __name__ == "__main__":
    main()
