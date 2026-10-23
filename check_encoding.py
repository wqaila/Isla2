# -*- coding: utf-8 -*-
import os

filepath = r'd:\xiangmudaima\zjy9\website\projects.html'

with open(filepath, 'rb') as f:
    data = f.read()

print(f"File size: {len(data)} bytes")
print(f"BOM: {data[:3].hex()} (should be efbbbf for UTF-8 BOM)")

# Try to decode the title bytes
title_start = data.find(b'<title>')
title_end = data.find(b'</title>')
if title_start >= 0:
    title_bytes = data[title_start+7:title_end]
    print(f"\n<title> raw bytes (first 50): {title_bytes[:50].hex()}")
    try:
        title_decoded = title_bytes.decode('utf-8')
        print(f"Decoded as UTF-8: {title_decoded}")
    except Exception as e:
        print(f"UTF-8 decode failed: {e}")

# Check the meta description
desc_start = data.find(b'<meta name="description"')
desc_end = data.find(b'>', desc_start)
if desc_start >= 0:
    desc_line = data[desc_start:desc_end+1]
    try:
        desc_decoded = desc_line.decode('utf-8')
        print(f"\nDescription decoded: {desc_decoded[:100]}")
    except Exception as e:
        print(f"\nDescription decode failed: {e}")

# Check line 6 (meta description)
lines = data.split(b'\n')
print(f"\nLine 6 (raw): {lines[5][:100]}")
try:
    print(f"Line 6 (utf-8): {lines[5].decode('utf-8')[:100]}")
except Exception as e:
    print(f"Line 6 decode failed: {e}")

# Check some text content
h1_start = data.find(b'<h1')
if h1_start >= 0:
    h1_close = data.find(b'>', h1_start)
    h1_end = data.find(b'</h1>', h1_close)
    h1_bytes = data[h1_close+1:h1_end]
    try:
        h1_decoded = h1_bytes.decode('utf-8')
        print(f"\nFirst H1 decoded: {h1_decoded[:80]}")
    except Exception as e:
        print(f"\nH1 decode failed: {e}")

# Character frequency check
print(f"\n--- Byte frequency analysis for non-ASCII ---")
non_ascii = bytes([b for b in data if b > 127])
print(f"Non-ASCII bytes: {len(non_ascii)} ({len(non_ascii)/len(data)*100:.1f}%)")