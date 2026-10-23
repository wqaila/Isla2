# -*- coding: utf-8 -*-
"""
修复 projects.html 的编码乱码问题
分析各种可能的编解码错误链并尝试恢复
"""
import os

filepath = r'd:\xiangmudaima\zjy9\website\projects.html'

# 读取原始文件（作为UTF-8，这是损坏后的文件）
with open(filepath, 'r', encoding='utf-8') as f:
    garbled_content = f.read()

# 获取标题乱码文本
import re
title_match = re.search(r'<title>([^<]+)</title>', garbled_content)
garbled_title = title_match.group(1) if title_match else ""

# 假设正确的标题
correct_title = "次元萌盒 · 项目全景汇总"

result_lines = []
result_lines.append("=" * 60)
result_lines.append("编码损坏分析")
result_lines.append("=" * 60)
result_lines.append(f"乱码标题: {garbled_title}")
result_lines.append(f"预期标题: {correct_title}")
result_lines.append("")

# 分析乱码标题的每个字符
result_lines.append("乱码标题字符和UTF-8字节:")
for c in garbled_title:
    b = c.encode('utf-8')
    result_lines.append(f"  [{c}] U+{ord(c):04X} -> UTF-8: {b.hex()}")
result_lines.append("")

# 预期标题字符和UTF-8字节
result_lines.append("预期标题字符和UTF-8字节:")
for c in correct_title:
    b = c.encode('utf-8')
    result_lines.append(f"  [{c}] U+{ord(c):04X} -> UTF-8: {b.hex()}")
result_lines.append("")

# 尝试各种恢复方法
result_lines.append("=" * 60)
result_lines.append("尝试恢复方法")
result_lines.append("=" * 60)

# 方法1: 把乱码当作被错误解码的字节流来处理
# 如果原始正确UTF-8字节被某个错误编码解析后重新保存为UTF-8
# 我们需要找到那个错误的编解码链

# 思路：乱码文本 -> 编码为某种格式 -> 解码为UTF-8 -> 得到正确文本
# 反过来：正确文本 -> UTF-8字节 -> 错误解码 -> 错误编码 -> 得到乱码文本
# 
# 常见的错误链：
# 1. 原始UTF-8 -> 被当作Latin-1/CP1252读取 -> 重新保存为UTF-8
# 2. 原始UTF-8 -> 被当作GBK读取 -> 重新保存为UTF-8  
# 3. 原始UTF-8 -> 被当作GB18030读取 -> 重新保存为UTF-8

# 测试方法1: Latin-1 (ISO-8859-1)
try:
    recovered = garbled_title.encode('latin-1').decode('utf-8')
    result_lines.append(f"Latin-1恢复: {recovered}")
except Exception as e:
    result_lines.append(f"Latin-1恢复失败: {e}")

# 测试方法2: CP1252 (Windows Latin-1)
try:
    recovered = garbled_title.encode('cp1252').decode('utf-8')
    result_lines.append(f"CP1252恢复: {recovered}")
except Exception as e:
    result_lines.append(f"CP1252恢复失败: {e}")

# 测试方法3: GBK
try:
    recovered = garbled_title.encode('gbk').decode('utf-8')
    result_lines.append(f"GBK恢复: {recovered}")
except Exception as e:
    result_lines.append(f"GBK恢复失败: {e}")

# 测试方法4: GB18030
try:
    recovered = garbled_title.encode('gb18030').decode('utf-8')
    result_lines.append(f"GB18030恢复: {recovered}")
except Exception as e:
    result_lines.append(f"GB18030恢复失败: {e}")

# 测试方法5: 反向：正确文本 -> bytes -> 尝试各种错误解码
result_lines.append("")
result_lines.append("--- 从正确文本反向推导 ---")
correct_bytes = correct_title.encode('utf-8')

# 如果正确UTF-8字节被当作Latin-1读取会得到什么？
wrong1 = correct_bytes.decode('latin-1')  
result_lines.append(f"UTF-8->Latin-1解码: {wrong1}")
# 这些错误字符再编码为UTF-8就是损坏文件中的内容
rewrong1 = wrong1.encode('utf-8')
result_lines.append(f"  再编码为UTF-8字节: {rewrong1.hex()}")

# 如果正确UTF-8字节被当作GBK读取会得到什么？
try:
    wrong2 = correct_bytes.decode('gbk')
    result_lines.append(f"UTF-8->GBK解码: {wrong2}")
    rewrong2 = wrong2.encode('utf-8')
    result_lines.append(f"  再编码为UTF-8字节: {rewrong2.hex()}")
except Exception as e:
    result_lines.append(f"UTF-8->GBK解码失败: {e}")

# 如果正确UTF-8字节被当作CP1252读取会得到什么？
try:
    wrong3 = correct_bytes.decode('cp1252')
    result_lines.append(f"UTF-8->CP1252解码: {wrong3}")
    rewrong3 = wrong3.encode('utf-8')
    result_lines.append(f"  再编码为UTF-8字节: {rewrong3.hex()}")
except Exception as e:
    result_lines.append(f"UTF-8->CP1252解码失败: {e}")

# 分析原始损坏文件的原始字节
result_lines.append("")
result_lines.append("--- 原始文件字节分析 ---")
with open(filepath, 'rb') as f:
    raw = f.read()

# 获取title的原始字节
ts = raw.find(b'<title>')
te = raw.find(b'</title>')
if ts >= 0 and te >= 0:
    title_raw = raw[ts+7:te]
    result_lines.append(f"Title原始字节: {title_raw.hex()}")
    result_lines.append(f"Title原始字节长度: {len(title_raw)}")

# 写入分析结果
output_path = r'd:\xiangmudaima\encoding_analysis.txt'
with open(output_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(result_lines))

print(f"分析结果已写入: {output_path}")
print("Done.")