#!/usr/bin/env bash
# 修复被污染的参考文本：从 zjy13 的原始 .txt 重新导入。
set -uo pipefail
source "${HOME}/cosyvoice-venv/bin/activate"
cd /mnt/d/xiangmudaima/zjy14 || exit 1
export PYTHONPATH=/mnt/d/xiangmudaima/zjy14

python - <<'PY'
from pathlib import Path
from custom_voice.db.session import Database

SRC_TXT = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt")
PREFIX = "You are a helpful assistant.<|endofprompt|>"

raw = SRC_TXT.read_text(encoding="utf-8").strip()
correct = raw if "<|endofprompt|>" in raw else PREFIX + raw

db = Database()
try:
    conn = db.connect()
    row = conn.execute(
        "SELECT transcript FROM speakers WHERE speaker_id='elysia'"
    ).fetchone()
    before = row["transcript"] if row else ""
    print(f"修复前: {before!r}")
    print(f"        {len(before)} 字符")
    print(f"修复后: {correct!r}")
    print(f"        {len(correct)} 字符")

    if before == correct:
        print("已正确，无需修复")
    else:
        with db.transaction() as tx:
            tx.execute(
                "UPDATE speakers SET transcript=? WHERE speaker_id='elysia'",
                (correct,),
            )
        print("已修复")
finally:
    db.close()
PY
