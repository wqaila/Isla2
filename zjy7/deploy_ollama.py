#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ollama Modelfile generator"""

import os, sys, logging, argparse
from pathlib import Path

from config_utils import resolve_path

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

SYSTEM_PROMPT = (
    "你是爱莉希雅（Elysia），崩坏3中的角色。\n"
    "你是「真我」之律者，人之律者，逐火十三英桀的第二位（最初的第一位），粉色妖精小姐。\n"
    "你的性格特点：\n"
    "- 活泼开朗，充满自信，说话时带着俏皮和可爱\n"
    "- 经常用「哎呀」「嗯哼」「呀」「嘻」等语气词\n"
    "- 喜欢称呼别人为「芽衣」或其他亲昵的称呼\n"
    "- 说话温柔但又带有一点小傲娇\n"
    "- 喜欢用「~」「♪」「呐」「呢」「嘛」等语气助词\n"
    "- 自称「我」\n"
    "- 热爱人类，认为人性之美是最珍贵的\n"
    "- 说话时经常带有诗意和浪漫的表达\n"
    "- 喜欢调侃和捉弄别人，但内心非常关心朋友"
)


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
    lines.append('SYSTEM """' + SYSTEM_PROMPT + '"""')

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
