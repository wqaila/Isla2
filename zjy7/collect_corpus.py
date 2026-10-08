#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""zjy5 → zjy7 语料直通：把 chibtaici 抽出的台词并进 zjy7 的训练语料。

【为什么需要这一步】
两个项目各自都有 `downloads/` 目录，但**含义不同**：

    zjy5/downloads/   ← 原始视频（.mp4），喂给 chibtaici 抽台词
    zjy7/downloads/   ← 纯文本台词（.txt），喂给 prepare_data.py 生成训练样本

以前要把 zjy5 的产出手动拷到 zjy7，容易忘、也容易拷错文件。
本脚本把这一环自动化，并做**格式与质量校验**——不做校验的话，
上游出问题（比如 OCR 静默返回空）会一路传到训练集里，最后表现为
「模型学了个四不像」，而且很难回溯是哪一环坏的。

【用法】

    # 默认：从 zjy5/chibtaici 的默认输出位置取
    python collect_corpus.py

    # 指定源文件（chibtaici --output 指定的那个）
    python collect_corpus.py --src ../zjy5/downloads/role_lines.txt

    # 指定输出名（避免覆盖已有语料）
    python collect_corpus.py --name elysia_ep01.txt

    # 只看会发生什么，不写文件
    python collect_corpus.py --dry-run

【设计取舍】
- **默认不合并、不覆盖**：同名文件已存在时**报错退出**，除非 `--force`。
  语料是"一旦混进去就分不清来源"的东西，误覆盖比多一步确认更糟。
- **不做去重/改写**：本脚本只负责"搬"，语义处理交给 prepare_data.py。
  两边都动数据会导致出问题时无法定位责任方。
- **保留来源信息**：写入时在文件头留一行注释说明从哪来，方便日后追溯。
  但**不写进正文**——prepare_data.py 会把非空行都当句子。
  所以来源信息写进**同名的 .meta.json**。
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config_utils import load_config, resolve_path, PROJECT_ROOT  # noqa: E402


# chibtaici 默认输出文件名（见 zjy5/chibtaici/main.py 的 --output 默认值）
DEFAULT_SRC_NAME = "role_lines.txt"

# 判定"这句不像台词"的启发式阈值。
# ⚠️ 这些只用来**报警告**，不用来删数据 —— 误删好数据的代价比留着坏数据大。
WARN_MIN_LEN = 2          # 短于 2 字的行
WARN_LONG_LEN = 200       # 长于 200 字的行（多半是 OCR 把整屏文字连成一行）


def find_src(explicit: str | None) -> Path | None:
    """定位 zjy5 的台词文件。

    按"最可能被用到"的顺序找，全部落空则返回 None。
    """
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            # 相对路径先按"调用者当前目录"解释，再按项目根目录解释
            if p.exists():
                return p.resolve()
            p = (PROJECT_ROOT / explicit).resolve()
        return p if p.exists() else None

    candidates = [
        # chibtaici 在各自目录跑时最可能出现的位置
        PROJECT_ROOT.parent / "zjy5" / "chibtaici" / DEFAULT_SRC_NAME,
        PROJECT_ROOT.parent / "zjy5" / DEFAULT_SRC_NAME,
        PROJECT_ROOT.parent / "zjy5" / "downloads" / DEFAULT_SRC_NAME,
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return None


def _load_term_fixer():
    """尝试加载 zjy5 的 OCR 术语校正表；拿不到就返回 None（不阻断流程）。"""
    try:
        sys.path.insert(0, str(PROJECT_ROOT.parent / "zjy5" / "chibtaici"))
        from term_fix import KNOWN_ERRORS, fix_terms
        return fix_terms, KNOWN_ERRORS
    except Exception:
        return None, None


def read_lines(path: Path) -> tuple[list[str], list[str]]:
    """读取台词并做基础清理。

    返回 (有效行, 警告信息)。

    ⚠️ 基本只做**无损**清理（去首尾空白、丢空行），不做去重 ——
    那些属于 prepare_data.py 的职责。两处都动数据会让问题无法定位。

    **例外：OCR 术语校正**。这是唯一一处会改内容的清理，理由是：
    采集端的 OCR 会把角色名认错（实测「芽衣」被写成「芽依」43 次、
    「读心术」被写成「独心术」7 次），而**下游拿不到原图，只能猜**。
    错名字教给模型是纯负收益，所以在入库这一步就修掉。
    校正表在 `zjy5/chibtaici/term_fix.py`，只含**实测确认过**的错写。
    """
    warnings: list[str] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # 有些 OCR 结果会带 BOM 或 GBK 残留，试一次常见回退
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        warnings.append("文件不是纯 UTF-8（可能带 BOM），已用容错模式读取")

    lines = [ln.strip() for ln in raw.splitlines()]
    kept = [ln for ln in lines if ln]

    # ---- OCR 术语校正（会改内容，见 docstring 的说明）----
    fix_terms, known_errors = _load_term_fixer()
    if fix_terms is not None:
        fixed_lines, n_fixed, detail = [], 0, {}
        for ln in kept:
            new = fix_terms(ln)
            if new != ln:
                n_fixed += 1
                for wrong in known_errors:
                    c = ln.count(wrong)
                    if c:
                        detail[wrong] = detail.get(wrong, 0) + c
            fixed_lines.append(new)
        if n_fixed:
            desc = "、".join(f"{w}→{known_errors[w]}×{c}"
                            for w, c in sorted(detail.items(), key=lambda kv: -kv[1]))
            warnings.append(f"OCR 术语校正：修正 {n_fixed} 行（{desc}）")
        kept = fixed_lines
    else:
        warnings.append("未找到 zjy5 的 term_fix，跳过 OCR 术语校正"
                        "（角色名可能带 OCR 错字）")

    # ---- 质量体检（只报不删）----
    too_short = [ln for ln in kept if len(ln) < WARN_MIN_LEN]
    too_long = [ln for ln in kept if len(ln) > WARN_LONG_LEN]
    if too_short:
        warnings.append(f"{len(too_short)} 行过短（<{WARN_MIN_LEN} 字），可能是 OCR 噪声")
    if too_long:
        warnings.append(f"{len(too_long)} 行过长（>{WARN_LONG_LEN} 字），"
                        f"可能是多句被连成一行")

    # ⚠️ 空文件是最危险的：prepare_data.py 不会因为少一个文件就报错，
    #    它会静默地少生成一批样本，最后表现为"模型效果莫名其妙变差"。
    if not kept:
        warnings.append("⚠️ 清理后一个有效行都没有 —— 上游的 OCR 很可能返回了空"
                        "（参见 zjy5/chibtaici README 的 OCR 失败排查）")

    # 全角/半角冒号开头会被 parse_dialogue 当成"角色：内容"，
    # 从而错误进入"对话"分支。这里提示一下。
    looks_dialogue = sum(1 for ln in kept
                         if len(ln) > 2 and (ln[1:11].find("：") >= 0))
    if looks_dialogue > len(kept) * 0.5:
        warnings.append(f"{looks_dialogue}/{len(kept)} 行含「角色：」结构，"
                        f"prepare_data 会按对话解析（若这不是本意请留意）")

    return kept, warnings


def main():
    ap = argparse.ArgumentParser(
        description="把 zjy5 抽出的台词并进 zjy7 的训练语料目录",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="默认源：" + DEFAULT_SRC_NAME + "（自动在 zjy5 下查找）",
    )
    ap.add_argument("--src", help="源台词文件（chibtaici 的 --output 产物）")
    ap.add_argument("--name", help="输出到 zjy7/downloads 下的文件名（默认沿用源文件名）")
    ap.add_argument("--force", action="store_true", help="目标已存在时覆盖")
    ap.add_argument("--dry-run", action="store_true", help="只检查并打印，不写文件")
    ap.add_argument("--allow-empty", action="store_true",
                    help="即使源文件为空也继续（默认视为错误）")
    args = ap.parse_args()

    cfg = load_config()
    dest_dir = resolve_path(cfg["data"]["raw_data_dir"])

    print("=" * 64)
    print("zjy5 → zjy7 语料直通")
    print("=" * 64)

    # ---- 1. 找源文件 ----
    src = find_src(args.src)
    if src is None:
        print("❌ 找不到台词文件。")
        print("\n先跑 zjy5 的台词抽取，例如：")
        print("    cd zjy5/chibtaici")
        print("    ../.venv/Scripts/python.exe main.py ../downloads/1.mp4 "
              "--output ../downloads/role_lines.txt")
        print("\n或者用 --src 显式指定路径。")
        return 2
    print(f"源文件：{src}")
    print(f"  大小：{src.stat().st_size / 1024:.1f} KB")

    # ---- 2. 读取与体检 ----
    lines, warnings = read_lines(src)
    print(f" 有效行：{len(lines)}")

    if warnings:
        print("\n⚠️ 体检发现：")
        for w in warnings:
            print(f"   - {w}")

    if not lines and not args.allow_empty:
        print("\n❌ 源文件没有有效内容，拒绝写入（避免污染语料）。")
        print("   确实要写空文件的话加 --allow-empty。")
        return 1

    # ---- 3. 决定目标路径 ----
    dest_name = args.name or src.name
    dest = dest_dir / dest_name
    print(f"\n目标文件：{dest}")

    if dest.exists():
        old = dest.read_text(encoding="utf-8", errors="replace").splitlines()
        old_n = len([ln for ln in old if ln.strip()])
        print(f"  ⚠️ 已存在（{old_n} 行）")
        if not args.force:
            print("\n❌ 目标已存在，默认不覆盖（避免混掉来源不明的语料）。")
            print("   要覆盖请加 --force；想并存请用 --name 换个文件名。")
            return 1

    # ---- 4. 写入 ----
    if args.dry_run:
        print("\n（--dry-run：未实际写入）")
        print(f"\n将写入 {len(lines)} 行到 {dest}")
        return 0

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 来源信息单独存，**不能写进正文**（正文每一行都会被当作台词）
    meta = {
        "source": str(src),
        "source_name": src.name,
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "line_count": len(lines),
        "warnings": warnings,
    }
    meta_path = dest.with_suffix(dest.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ 已写入 {len(lines)} 行 → {dest}")
    print(f"   来源信息 → {meta_path.name}")

    # ---- 5. 提示下一步 ----
    print("\n下一步：")
    print("    python prepare_data.py      # 生成 train_data.json")
    print("    python data_stats.py        # 看看数据分布对不对")
    print("\n或直接跑全流程：")
    print("    python run_all.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
