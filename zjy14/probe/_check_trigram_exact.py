"""精确验证 trigram 分词器的行为，确定中文搜索方案。"""
from __future__ import annotations

import sqlite3


def main() -> int:
    print("=" * 90)
    print(" trigram 分词器精确行为")
    print("=" * 90)

    c = sqlite3.connect(":memory:")
    c.execute("CREATE VIRTUAL TABLE t USING fts5(body, tokenize='trigram')")
    c.execute("INSERT INTO t VALUES ('今天天气不错')")
    c.execute("INSERT INTO t VALUES ('明天要下雨')")
    c.execute("INSERT INTO t VALUES ('hello world')")

    print("\n  数据: '今天天气不错' / '明天要下雨' / 'hello world'")
    print(f"\n  {'查询':<16}{'命中数':>8}  说明")
    print("  " + "-" * 60)

    cases = [
        ("天气", "2 字"),
        ("天气不", "3 字"),
        ("今天天", "3 字"),
        ("今天天气", "4 字"),
        ("天", "1 字"),
        ("下雨", "2 字"),
        ("要下雨", "3 字"),
        ("hello", "英文 5 字"),
        ("ell", "英文 3 字"),
    ]

    for q, note in cases:
        try:
            r = c.execute(
                "SELECT COUNT(*) FROM t WHERE t MATCH ?", (f'"{q}"',)
            ).fetchone()[0]
            print(f"  {q:<16}{r:>8}  {note}")
        except Exception as e:  # noqa: BLE001
            print(f"  {q:<16}{'ERR':>8}  {e}")

    print()
    print("=" * 90)
    print(" 结论")
    print("=" * 90)
    print("""  trigram 要求查询串至少 3 个字符，2 字查询返回 0。

  因此需要「混合策略」：
    查询长度 >= 3  -> 走 FTS5 trigram 索引（快）
    查询长度 < 3   -> 走 LIKE（慢但正确）

  这个判断必须自动做，不能要求调用方自己选。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
