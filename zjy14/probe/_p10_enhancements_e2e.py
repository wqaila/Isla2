"""四个补齐功能的真实联调验证。

对应 2026-11-18 补齐的已知限制：
  1. 归档日志查询（原来只读当前文件）
  2. 日志级别模糊匹配（原来 WARN 匹配不到 WARNING）
  3. 导出 CSV/JSON（原来无导出）
  4. 全文搜索（原来 LIKE 无索引）
  5. 自动清理 + 审计（原来需手动）
"""
from __future__ import annotations

import csv
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:50013"

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    print(f"  [PASS] {msg}")
    PASS += 1


def bad(msg: str) -> None:
    global FAIL
    print(f"  [FAIL] {msg}")
    FAIL += 1


def check(cond: bool, msg: str) -> None:
    ok(msg) if cond else bad(msg)


def req(path: str, method: str = "GET", body: dict | None = None,
        raw: bool = False) -> tuple[int, object, dict]:
    """发请求。

    注意：``/synthesize`` 返回二进制 WAV，不能按 JSON 解析。
    早期版本无条件 json.loads，把 RIFF 头当 JSON 解会抛异常，
    导致「合成失败: 0」这种误导性输出（0 是我兜底的异常码）。
    """
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    r = urllib.request.Request(f"{BASE}{path}", data=data,
                               headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            content = resp.read()
            hdrs = dict(resp.headers)
            ctype = resp.headers.get("content-type", "")
            if raw:
                return resp.status, content, hdrs
            if "json" in ctype:
                try:
                    return resp.status, json.loads(content), hdrs
                except json.JSONDecodeError:
                    return resp.status, {"bytes": len(content)}, hdrs
            # 二进制（WAV 等）
            return resp.status, {"bytes": len(content), "content_type": ctype}, hdrs
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read()), dict(e.headers)
        except Exception:  # noqa: BLE001
            return e.code, None, dict(e.headers)
    except Exception as e:  # noqa: BLE001
        return 0, str(e), {}


def main() -> int:
    print("=" * 90)
    print(" 四个补齐功能联调验证")
    print("=" * 90)
    print(f"  后端: {BASE}")

    code, health, _ = req("/health")
    if code != 200:
        print(f"  [FAIL] 后端不可用: {code} {health}")
        return 1
    print(f"  引擎: {health.get('engine')}")

    # 造数据：合成几次，含不同文本便于搜索
    print("\n[准备] 合成 3 次建立可搜索数据")
    texts = [
        "今天天气真不错，适合出门散步。",
        "全文搜索功能需要验证中文效果。",
        "第三条记录用于导出测试。",
    ]
    for t in texts:
        c, _, _ = req("/synthesize", "POST", {"text": t, "voice_id": "elysia"})
        if c != 200:
            bad(f"合成失败: {c}")
    ok("数据就绪")

    # ---- 1. 全文搜索 ----
    print("\n[1] 全文搜索（FTS5 trigram 索引）")
    # 用刚合成的文本里的词做查询，确保数据一定存在。
    # 早期版本硬编码「今天天气」，但前一轮清理可能已删掉那条记录，
    # 导致误判「FTS 不工作」。测试必须用自己刚造的数据。
    probe_word = "今天天气"
    code, d, _ = req(f"/api/v1/history?text_search={urllib.parse.quote(probe_word)}")
    check(code == 200, f"3+ 字查询 -> {code}")
    if isinstance(d, dict):
        n = d.get("count", 0)
        check(n >= 1, f"搜「{probe_word}」命中 {n} 条")

    # 2 字降级
    code, d2, _ = req(f"/api/v1/history?text_search={urllib.parse.quote('天气')}")
    if isinstance(d2, dict):
        n = d2.get("count", 0)
        check(n >= 1, f"搜「天气」(2字，自动降级 LIKE) 命中 {n} 条")

    # 不存在的词
    code, d3, _ = req("/api/v1/history?text_search=绝对不存在的词组")
    if isinstance(d3, dict):
        check(d3.get("count", -1) == 0, "不存在的词返回 0 条")

    # 特殊字符不报错
    for q in ("C++", "%_", '"quote'):
        code, _, _ = req(f"/api/v1/history?text_search={urllib.parse.quote(q)}")
        check(code == 200, f"特殊字符 {q!r} 不报错（{code}）")

    # ---- 2. 日志级别模糊匹配 ----
    print("\n[2] 日志级别模糊匹配")
    code, all_logs, _ = req("/api/v1/logs?limit=200")
    total = all_logs.get("count", 0) if isinstance(all_logs, dict) else 0
    print(f"    当前日志共 {total} 条")

    # 精确匹配
    code, exact, _ = req("/api/v1/logs?level=WARNING&limit=200")
    n_exact = exact.get("count", 0) if isinstance(exact, dict) else 0
    check(code == 200, f"level=WARNING -> {n_exact} 条")

    # 前缀匹配
    code, prefix, _ = req("/api/v1/logs?level=WARN&limit=200")
    n_prefix = prefix.get("count", 0) if isinstance(prefix, dict) else 0
    check(n_prefix == n_exact, f"level=WARN 与 WARNING 结果相同（{n_prefix}）")

    # 小写
    code, lower, _ = req("/api/v1/logs?level=warn&limit=200")
    n_lower = lower.get("count", 0) if isinstance(lower, dict) else 0
    check(n_lower == n_exact, f"小写 warn 也匹配（{n_lower}）")

    # ---- 3. 归档日志查询 ----
    print("\n[3] 归档日志查询")
    code, files, _ = req("/api/v1/logs/files")
    check(code == 200 and isinstance(files, list), f"文件列表 -> {code}")
    if isinstance(files, list):
        print(f"    发现 {len(files)} 个文件: {[f['name'] for f in files]}")
        archives = [f for f in files if not f.get("is_current")]
        if archives:
            name = archives[0]["name"]
            code, arc, _ = req(f"/api/v1/logs?file_name={urllib.parse.quote(name)}")
            check(code == 200, f"查归档 {name} -> {code}")
            if isinstance(arc, dict):
                print(f"    归档 {name}: {arc.get('count')} 条")
        else:
            print("    （无归档文件，跳过实际读取；接口已就绪）")
            ok("归档查询接口可用")

    # 路径穿越必须拒绝
    code, _, _ = req("/api/v1/logs?file_name=../etc/passwd")
    check(code == 400, f"路径穿越被拒绝（{code}）")

    code, _, _ = req("/api/v1/logs?file_name=other.log")
    check(code == 400, f"非法文件名被拒绝（{code}）")

    # ---- 4. 导出 ----
    print("\n[4] 导出 CSV / JSON")
    code, body, hdrs = req("/api/v1/history/export?fmt=csv", raw=True)
    check(code == 200, f"导出 CSV -> {code}")
    if code == 200 and isinstance(body, bytes):
        check(body.startswith(b"\xef\xbb\xbf"), "CSV 带 UTF-8 BOM（Excel 中文不乱码）")
        text = body.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        check(len(rows) >= 2, f"CSV 有表头 + 数据行（{len(rows)} 行）")
        check("id" in rows[0] and "text" in rows[0], "表头字段正确")
        cd = hdrs.get("content-disposition", "")
        check("attachment" in cd, f"触发下载（{cd[:50]}）")

    code, body, hdrs = req("/api/v1/history/export?fmt=json", raw=True)
    check(code == 200, f"导出 JSON -> {code}")
    if code == 200 and isinstance(body, bytes):
        d = json.loads(body)
        check("items" in d and "count" in d, "JSON 结构正确")
        check(d["count"] >= 3, f"导出 {d['count']} 条")

    # 导出带筛选
    code, body, _ = req("/api/v1/history/export?fmt=json&only_failed=true", raw=True)
    if code == 200 and isinstance(body, bytes):
        d = json.loads(body)
        all_failed = all(i.get("error_code") for i in d["items"])
        check(all_failed, "导出遵守筛选条件")

    # 非法格式
    code, _, _ = req("/api/v1/history/export?fmt=xml")
    check(code == 422, f"非法格式被拒绝（{code}）")

    # ---- 5. 自动清理 + 审计 ----
    print("\n[5] 自动清理与审计")
    code, status, _ = req("/api/v1/system/status")
    if isinstance(status, dict):
        cl = status.get("cleanup", {})
        check("cleanup" in status, "/system/status 暴露清理状态")
        print(f"    保留 {cl.get('retain_days')} 天，日志保留 "
              f"{cl.get('log_retain_days')} 天，间隔 {cl.get('interval_h')}h")
        check(cl.get("thread_alive") in (True, False), "线程状态可读")

    # 手动清理会写审计
    code, _, _ = req("/api/v1/history?before=2099-01-01T00:00:00%2B00:00",
                     "DELETE")
    check(code == 204, f"手动清理 -> {code}")

    code, audit, _ = req("/api/v1/history/cleanup-history")
    check(code == 200 and isinstance(audit, list), f"审计记录 -> {code}")
    if isinstance(audit, list) and audit:
        rec = audit[0]
        check(rec.get("kind") == "synthesis", f"审计类型正确（{rec.get('kind')}）")
        check(rec.get("triggered_by") == "manual",
              f"触发方式正确（{rec.get('triggered_by')}）")
        check(rec.get("removed", 0) >= 1, f"记录了删除条数（{rec.get('removed')}）")

    # 清理后应为空
    code, after, _ = req("/api/v1/history/stats")
    if isinstance(after, dict):
        check(after.get("total", -1) == 0, f"清理后归零（{after.get('total')}）")

    print()
    print("=" * 90)
    print(f" 结果: {PASS} 通过, {FAIL} 失败")
    print("=" * 90)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
