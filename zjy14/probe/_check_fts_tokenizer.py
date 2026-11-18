"""确认 FTS5 对中文的分词行为，选正确的方案。"""
from __future__ import annotations

import sqlite3


def test_tokenizer(name: str, tokenize: str) -> None:
    """测试某个分词器对中文的处理。"""
    c = sqlite3.connect(":memory:")
    try:
        c.execute(
            f"CREATE VIRTUAL TABLE t USING fts5(body, tokenize='{tokenize}')"
        )
    except Exception as e:  # noqa: BLE001
        print(f"  {name:<20} 创建失败: {e}")
        return

    c.execute("INSERT INTO t VALUES ('今天天气不错')")
    c.execute("INSERT INTO t VALUES ('明天要下雨')")

    print(f"  {name:<20}", end="")

    # 整词匹配
    try:
        r = c.execute("SELECT COUNT(*) FROM t WHERE t MATCH '天气'").fetchone()[0]
        print(f"搜'天气'={r}", end="  ")
    except Exception as e:  # noqa: BLE001
        print(f"搜'天气'失败({e})", end="  ")

    # 前缀匹配
    try:
        r = c.execute("SELECT COUNT(*) FROM t WHERE t MATCH '天*'").fetchone()[0]
        print(f"搜'天*'={r}", end="  ")
    except Exception as e:  # noqa: BLE001
        print(f"搜'天*'失败({e})", end="  ")

    # 整句
    try:
        r = c.execute(
            "SELECT COUNT(*) FROM t WHERE t MATCH '\"今天天气不错\"'"
        ).fetchone()[0]
        print(f"整句={r}")
    except Exception as e:  # noqa: BLE001
        print(f"整句失败({e})")


def main() -> int:
    print("=" * 90)
    print(" FTS5 中文分词行为")
    print("=" * 90)
    print("  测试数据: '今天天气不错' / '明天要下雨'")
    print()

    # unicode61 是默认分词器，按 Unicode 边界切分
    test_tokenizer("unicode61", "unicode61")
    # porter 是英文词干，对中文无意义
    test_tokenizer("porter", "porter")
    # trigram：SQLite 3.34+ 支持，按 3 字符滑窗，适合子串搜索
    test_tokenizer("trigram", "trigram")

    print()
    print("=" * 90)
    print(" 结论")
    print("=" * 90)
    print("""  unicode61：把「今天天气不错」当成一个完整 token，
             搜「天气」匹配不到（不是子串搜索）。
             只有搜完整词「今天天气不错」才能命中。

  trigram：按 3 字符滑窗建索引，支持任意子串搜索，
           适合中文（中文词短，3 字窗口能覆盖大部分查询）。
           代价：索引更大，且要求查询至少 3 个字符。

  方案：改用 trigram 分词器。
        代价是查询词需 >= 3 字符 —— 对中文来说可接受
        （2 字词用 LIKE 降级即可）。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
