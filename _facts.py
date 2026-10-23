import json
from pathlib import Path

out = []

# 1. 角色卡规模
p = Path(r'D:\xiangmudaima\zjy9\server\characters\elysia.json')
c = json.loads(p.read_text(encoding='utf-8'))
out.append("=== 角色卡 elysia.json ===")
out.append("system_prompt 字符数: %d" % len(c.get('system_prompt') or ''))
out.append("few_shot 消息条数: %d" % len(c.get('few_shot') or []))
out.append("greeting 长度: %d" % len(c.get('greeting') or ''))
out.append("字段: %s" % sorted(c.keys()))

# 2. 训练数据规模
p2 = Path(r'D:\xiangmudaima\zjy7\train_data.json')
out.append("")
out.append("=== zjy7 训练数据 ===")
if p2.exists():
    d = json.loads(p2.read_text(encoding='utf-8'))
    out.append("样本条数: %d" % (len(d) if isinstance(d, list) else 'n/a'))
    if isinstance(d, list) and d:
        first = d[0]
        keys = sorted(first.keys()) if isinstance(first, dict) else type(first).__name__
        out.append("单条字段: %s" % keys)
        out.append("单条预览(前120字): %s" % json.dumps(first, ensure_ascii=False)[:120])
else:
    out.append("不存在")

# 3. 评估集规模
p3 = Path(r'D:\xiangmudaima\zjy7\eval_data.json')
out.append("")
out.append("=== zjy7 独立评估集 ===")
if p3.exists():
    raw = p3.read_text(encoding='utf-8')
    try:
        d3 = json.loads(raw)
        out.append("条目数: %d" % len(d3))
    except Exception as e:
        out.append("解析失败: %s" % e)
        out.append("文件字符数: %d" % len(raw))
        out.append("出现次数的 'messages': %d" % raw.count('"messages"'))

# 4. 训练配置
p4 = Path(r'D:\xiangmudaima\zjy7\train_config.json')
out.append("")
out.append("=== zjy7 训练配置 ===")
if p4.exists():
    out.append(p4.read_text(encoding='utf-8')[:800])
else:
    out.append("不存在")

Path(r'D:\xiangmudaima\_facts.txt').write_text("\n".join(out), encoding='utf-8')
