#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""OCR 链路的集成冒烟测试（**需要 GPU/CPU 版 paddleocr + Pillow**，不在离线测试集里）。

它故意用「合成视频」而不是真番剧：可控、几秒钟跑完，还能把三件事一次说清：

1. OCR 能不能真的读到画面上的字幕
2. 字幕静止时**跳帧**是否生效（省掉多少次 OCR 调用）
3. 「相同文本才合并」是否把一条字幕聚成一条（而不是逐帧刷出一堆）

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/smoke_ocr.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ZJY5 = os.path.dirname(HERE)
Chibtaici = os.path.join(ZJY5, "chibtaici")
sys.path.insert(0, Chibtaici)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from main import RoleLineExtractor  # noqa: E402

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]

LINE_A = "悲剧并非终结，而是希望的起始。"
LINE_B = "这位可不是爱莉希雅，是本大爷说的。"


def pick_font(size=28):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    raise RuntimeError("找不到中文字体，无法生成合成视频")


def make_video(path, fps=25):
    """生成 240 帧（约 9.6 秒）的合成视频：有字幕 -> 无字幕 -> 另一句字幕。"""
    width, height = 640, 360
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    font = pick_font()

    for index in range(240):
        frame = Image.new("RGB", (width, height), (18, 18, 24))
        draw = ImageDraw.Draw(frame)
        draw.rectangle([0, int(height * 0.7), width, height], fill=(10, 10, 14))

        if index < 100:
            text = LINE_A
        elif index < 150:
            text = ""
        else:
            text = LINE_B

        if text:
            draw.text((30, int(height * 0.78)), text, font=font, fill=(245, 245, 245))

        writer.write(cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR))

    writer.release()


def main():
    with tempfile.TemporaryDirectory() as tmp:
        video = os.path.join(tmp, "sample.mp4")
        print("生成合成视频...")
        make_video(video)

        print("跑 OCR（跳帧开）...")
        extractor = RoleLineExtractor(
            video_path=video,
            enable_voice=False,
            ocr_interval=5,
            clean=False,
            ocr_skip_static=True,
        )
        entries = extractor.run(
            output_path=os.path.join(tmp, "role_lines.txt"), fmt="all"
        )

        print("\n识别结果：")
        for entry in entries:
            print(f"  [{entry['start']:6.2f} ~ {entry['end']:6.2f}] {entry['text']}")

        joined = "".join(e["text"] for e in entries)
        problems = []
        if LINE_A.rstrip("。") not in joined:
            problems.append("第一句字幕没识别出来")
        if LINE_B.rstrip("。") not in joined:
            problems.append("第二句字幕没识别出来")
        if not extractor._ocr_skipped:
            problems.append("跳帧一次都没生效（字幕是静止的，本应大量跳过）")
        if len(entries) > 6:
            problems.append(f"条目数 {len(entries)} 偏多，可能没按相同文本合并")

        print()
        if problems:
            for problem in problems:
                print(f"  ✗ {problem}")
            return 1
        print(f"  ✓ OCR 结果正确，跳帧 {extractor._ocr_skipped} 帧，"
              f"输出 {len(entries)} 条")
        return 0


if __name__ == "__main__":
    sys.exit(main())
