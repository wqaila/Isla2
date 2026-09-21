#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Training data statistics analysis tool"""

import json, logging, collections, sys, os
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# 无 tokenizer 时的保守估算系数（中文约 1 字 ≈ 1 token，英文更低），
# 仅作粗略参考，输出时会标注为「估算值」
FALLBACK_TOKENS_PER_CHAR = 1.0


def _get_tokenizer():
    """尝试用真实 tokenizer 统计；不可用（未装 transformers / 无模型）时返回 None"""
    try:
        from transformers import AutoTokenizer
    except Exception:
        logging.info('transformers 不可用，token 数将使用估算值')
        return None
    try:
        with open(Path(__file__).resolve().parent / 'train_config.json', 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        base = cfg['model']['base_model_path']
        if not os.path.isabs(base):
            base = str(Path(__file__).resolve().parent / base)
        return AutoTokenizer.from_pretrained(base, trust_remote_code=True)
    except Exception as e:
        logging.info('无法加载 tokenizer，token 数将使用估算值: ' + str(e))
        return None


def analyze(path='train_data.json'):
    if not Path(path).exists():
        logging.error('File not found: ' + path)
        return
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    tokenizer = _get_tokenizer()
    exact_tokens = tokenizer is not None

    total = len(data)
    logging.info('=== Training Data Stats (' + path + ') ===')
    logging.info('Total samples: ' + str(total))

    user_lens = []
    asst_lens = []
    token_est = []
    cat = collections.Counter()

    for item in data:
        msgs = item.get('messages', [])
        if len(msgs) < 3:
            continue
        uc = msgs[1].get('content', '')
        ac = msgs[2].get('content', '')
        user_lens.append(len(uc))
        asst_lens.append(len(ac))
        if exact_tokens:
            # 使用真实 tokenizer 计数（不含特殊 token）
            token_est.append(len(tokenizer(uc + ac, add_special_tokens=False)['input_ids']))
        else:
            token_est.append(int((len(uc) + len(ac)) * FALLBACK_TOKENS_PER_CHAR))

        # Chinese keyword category detection
        kw_id = ['你是谁', '叫什么', '介绍', '称号', '什么类型', '什么律者', '武器', '耳朵', '性格']
        kw_rl = ['芽衣', '凯文', '伊甸', '梅比乌斯', '樱', '千劫', '科斯魔', '阿波尼亚', '维尔薇', '菲莉丝', '格蕾修', '华', '苏']
        kw_wl = ['乐土', '逐火', '崩坏', '律者', '融合', '记忆体', '至深']
        kw_af = ['爱', '喜欢', '想你', '亲', '抱', '美', '可爱', '幸福', '开心']
        kw_dl = ['早上好', '晚安', '早安', '无聊', '天气', '跳舞', '季节', '忠告']
        kw_rj = ['AI', 'ChatGPT', '代码', '数学', '脏话', '政治', '黑进']
        kw_st = ['第一次见面', '摸过', '预言', '浪漫', '经典台词', '座右铭', '鼓励']

        matched = False
        for kw_list, cat_name in [
            (kw_id, 'identity'), (kw_rl, 'relationships'),
            (kw_wl, 'world'), (kw_af, 'affection'),
            (kw_dl, 'daily'), (kw_rj, 'rejection'), (kw_st, 'story')
        ]:
            if any(w in uc for w in kw_list):
                cat[cat_name] += 1
                matched = True
                break
        if not matched:
            cat['dialogue_other'] += 1

    logging.info('--- Length Stats ---')
    if user_lens:
        logging.info('User input: min=' + str(min(user_lens)) + ' max=' + str(max(user_lens)) + ' avg=' + str(sum(user_lens) // len(user_lens)))
    if asst_lens:
        logging.info('Asst reply: min=' + str(min(asst_lens)) + ' max=' + str(max(asst_lens)) + ' avg=' + str(sum(asst_lens) // len(asst_lens)))
    if token_est:
        label = 'Tokens' if exact_tokens else 'Est tokens (估算值)'
        logging.info(label + ': min=' + str(min(token_est)) + ' max=' + str(max(token_est)) + ' avg=' + str(sum(token_est) // len(token_est)))

    logging.info('--- Category Distribution ---')
    for c, count in cat.most_common():
        pct = count / total * 100
        bar = '#' * int(pct / 2)
        logging.info('  ' + c.ljust(20) + ': ' + str(count).rjust(4) + ' (' + ('%.1f' % pct) + '%) ' + bar)

    logging.info('--- Reply Length Distribution ---')
    buckets = [(0, 20), (20, 50), (50, 100), (100, 200), (200, 500), (500, 9999)]
    for lo, hi in buckets:
        count = sum(1 for l in asst_lens if lo <= l < hi)
        pct = count / total * 100 if total else 0
        label = str(lo) + '-' + str(hi) if hi < 9999 else str(lo) + '+'
        bar = '#' * int(pct / 2)
        logging.info('  ' + label.rjust(8) + ': ' + str(count).rjust(4) + ' (' + ('%.1f' % pct) + '%) ' + bar)

    logging.info('--- Duplicate Detection ---')
    ut = [i['messages'][1]['content'] for i in data if len(i.get('messages', [])) >= 2]
    uq = set(ut)
    logging.info('User: total=' + str(len(ut)) + ' unique=' + str(len(uq)) + ' dup=' + str(len(ut) - len(uq)))
    at = [i['messages'][2]['content'] for i in data if len(i.get('messages', [])) >= 3]
    aq = set(at)
    logging.info('Asst: total=' + str(len(at)) + ' unique=' + str(len(aq)) + ' dup=' + str(len(at) - len(aq)))

if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'train_data.json'
    analyze(path)
