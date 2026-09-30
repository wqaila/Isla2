#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ollama Modelfile generator"""

import os, sys, logging, argparse
from pathlib import Path

from config_utils import resolve_path
from persona import resolve_system_prompt

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# 与训练侧（prepare_data.py）共用同一份人设 —— 见 persona.py 的说明。
# 以前这里是第二份硬编码副本，虽然当时与训练逐字一致，但它和 zjy9 线上角色卡
# （客户端发的 system 消息会顶掉这里）对不上。现在统一从角色卡读。
SYSTEM_PROMPT, SYSTEM_SOURCE = resolve_system_prompt()


def find_gguf(gguf_dir):
    """Find GGUF file, prioritize Q8_0 > Q5_K_M > Q4_K_M > F16"""
    gguf_dir = Path(gguf_dir)
    if not gguf_dir.exists():
        return None
    gguf_files = list(gguf_dir.glob('*.gguf'))
    if not gguf_files:
        return None
    priority = ['Q8_0', 'Q5_K_M', 'Q4_K_M', 'F16']
    for p in priority:
        for f in gguf_files:
            if p in f.name:
                return f
    return gguf_files[0]


def generate_modelfile(gguf_path, output_path='Modelfile', temp=0.7, top_p=0.9, repeat_penalty=1.15, num_ctx=2048):
    """Generate Ollama Modelfile with Qwen2.5 ChatML template"""
    # 使用相对于 Modelfile 所在目录的相对路径，保证项目整体迁移后仍可用
    out_dir = Path(output_path).resolve().parent
    try:
        gguf_ref = os.path.relpath(Path(gguf_path).resolve(), out_dir).replace(chr(92), '/')
    except ValueError:
        # 跨盘符时无法求相对路径，退回绝对路径
        gguf_ref = os.path.abspath(str(gguf_path)).replace(chr(92), '/')
    if not gguf_ref.startswith('.'):
        gguf_ref = './' + gguf_ref

    # Build stop tokens using chr() to avoid template parsing issues
    # <|im_end|> and <|im_start|>
    im_start = chr(60) + '|im_start|' + chr(62)
    im_end = chr(60) + '|im_end|' + chr(62)

    # Build Qwen2.5 ChatML Go-template：
    # 每条消息输出 <|im_start|>role\ncontent<|im_end|>\n，末尾补 <|im_start|>assistant\n
    tmpl_parts = []
    tmpl_parts.append('{{- if .System }}' + im_start + 'system')
    tmpl_parts.append('{{ .System }}' + im_end)
    tmpl_parts.append('{{ end }}')
    tmpl_parts.append('{{- range .Messages }}' + im_start + '{{ .Role }}')
    tmpl_parts.append('{{ .Content }}' + im_end)
    tmpl_parts.append('{{ end }}')
    tmpl_parts.append(im_start + 'assistant')
    template_str = chr(10).join(tmpl_parts)

    lines = []
    lines.append('FROM ' + gguf_ref)
    lines.append('')
    lines.append('PARAMETER temperature ' + str(temp))
    lines.append('PARAMETER top_p ' + str(top_p))
    lines.append('PARAMETER repeat_penalty ' + str(repeat_penalty))
    lines.append('PARAMETER num_ctx ' + str(num_ctx))
    lines.append('PARAMETER stop "' + im_end + '"')
    lines.append('PARAMETER stop "' + im_start + '"')
    lines.append('')
    lines.append('TEMPLATE """' + template_str + '"""')
    lines.append('')
    # ⚠️ 闭合的 """ 前必须换行，不能直接拼在人设后面。
    #
    # 人设的结尾正好是一个引号（`…舰长开心就好！🎀"`），直接拼会得到
    # **四个连续引号** `""""`。这对 Modelfile 解析器是歧义的 ——
    # 它会把前三个当成结束符，剩下的一个变成游离 token（可能报错，
    # 也可能静默丢掉人设的最后一个字符）。
    # 顺带也让 check_persona.py 抠 SYSTEM 的正则能正确匹配。
    lines.append('SYSTEM """' + SYSTEM_PROMPT + chr(10) + '"""')

    content = chr(10).join(lines)
    Path(output_path).write_text(content, encoding='utf-8')

    logging.info('Modelfile generated: ' + output_path)
    logging.info('GGUF path: ' + gguf_ref)
    logging.info('')
    logging.info('Usage:')
    logging.info('  1. ollama create elysia -f ' + output_path)
    logging.info('  2. ollama run elysia')
    return output_path


def main():
    parser = argparse.ArgumentParser(description='Ollama Modelfile generator')
    parser.add_argument('--gguf', type=str, help='GGUF file path (auto-detect if not specified)')
    parser.add_argument('--output', type=str, default='Modelfile', help='Output path (default: Modelfile)')
    parser.add_argument('--temp', type=float, default=0.7, help='Temperature (default: 0.7)')
    parser.add_argument('--top-p', type=float, default=0.9, help='top_p (default: 0.9)')
    parser.add_argument('--num-ctx', type=int, default=2048, help='Context length (default: 2048)')
    parser.add_argument('--gguf-dir', type=str, default='gguf_output', help='GGUF directory (default: gguf_output)')
    args = parser.parse_args()

    # Find GGUF file
    gguf_path = args.gguf
    if not gguf_path:
        gguf_path = find_gguf(resolve_path(args.gguf_dir))
        if not gguf_path:
            logging.error('No GGUF file found! Run post_train.py first, or use --gguf to specify path')
            sys.exit(1)

    if not os.path.exists(str(gguf_path)):
        logging.error('GGUF file not found: ' + str(gguf_path))
        sys.exit(1)

    logging.info('Using GGUF: ' + str(gguf_path))
    generate_modelfile(gguf_path, output_path=str(resolve_path(args.output)),
                       temp=args.temp, top_p=args.top_p, num_ctx=args.num_ctx)


if __name__ == '__main__':
    main()
