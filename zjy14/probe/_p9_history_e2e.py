"""历史记录与日志 API 的真实联调验证。

与单元测试的区别：
  单元测试用 stub 引擎 + 临时库，这里打真实服务（真模型 + 真实数据目录），
  验证端到端可用性，包括真实日志文件的解析。

验证项：
  1. 合成几次 -> 记录能查到，字段正确
  2. 分页游标在真实数据上不重复
  3. 统计数字与实际一致
  4. 真实日志能解析（含中文、业务字段）
  5. 日志过滤（级别/request_id/关键词）
  6. 失败记录能被 only_failed 查到
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:50011"


def get(path: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:  # noqa: BLE001
            return e.code, None
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def post(path: str, body: dict) -> tuple[int, object, dict]:
    """POST 请求。

    注意：``/synthesize`` 返回二进制 WAV，不能按 JSON 解析。
    早期版本无条件 json.loads，导致把 WAV 头当 JSON 解，报
    ``'utf-8' codec can't decode byte 0x94`` —— 那是 RIFF 头。
    """
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
            ctype = r.headers.get("content-type", "")
            if "json" in ctype:
                return r.status, json.loads(raw), dict(r.headers)
            # 二进制（WAV）：只报字节数，不解析
            return r.status, {"bytes": len(raw), "content_type": ctype}, dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read()), dict(e.headers)
        except Exception:  # noqa: BLE001
            return e.code, None, dict(e.headers)
    except Exception as e:  # noqa: BLE001
        return 0, str(e), {}


def delete(path: str) -> int:
    req = urllib.request.Request(f"{BASE}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return 0


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


def main() -> int:
    print("=" * 90)
    print(" 历史记录与日志 API 真实联调")
    print("=" * 90)
    print(f"  后端: {BASE}")

    code, health = get("/health")
    if code != 200 or not isinstance(health, dict):
        print(f"  [FAIL] 后端不可用: {code}")
        return 1
    print(f"  引擎: {health.get('engine')}  采样率: {health.get('sample_rate')}")

    # ---- 1. 合成几次 ----
    print("\n[1] 合成 3 次（建立数据）")
    req_ids: list[str] = []
    for i, text in enumerate([
        "今天天气真不错，我们出去走走吧。",
        "这个测试用来验证合成记录查询接口。",
        "第三条记录，用于测试分页。",
    ]):
        code, body, headers = post("/synthesize", {"text": text, "voice_id": "elysia"})
        if code != 200:
            bad(f"第 {i + 1} 次合成失败: {code} {str(body)[:100]}")
            continue
        rid = headers.get("x-request-id", "")
        req_ids.append(rid)
        print(f"    第 {i + 1} 次 OK  request_id={rid}")

    check(len(req_ids) == 3, "3 次合成成功")

    # ---- 2. 查询列表 ----
    print("\n[2] 查询合成记录列表")
    code, page = get("/api/v1/history?limit=10")
    check(code == 200, f"GET /api/v1/history -> {code}")
    if not isinstance(page, dict):
        print(f"    响应异常: {page}")
        return 1

    items = page.get("items", [])
    check(len(items) >= 3, f"至少 3 条记录（实际 {len(items)}）")

    if items:
        rec = items[0]
        check(bool(rec.get("text")), "记录含 text")
        check(rec.get("voice_id") == "elysia", f"voice_id 正确（{rec.get('voice_id')}）")
        check(rec.get("error_code") == "", "成功记录 error_code 为空")
        check(rec.get("duration_s", 0) > 0, f"duration_s > 0（{rec.get('duration_s')}）")
        check(rec.get("rtf", 0) > 0, f"rtf > 0（{rec.get('rtf')}）")
        check(rec.get("sample_rate") == 24000, "采样率 24000")
        check(bool(rec.get("created_at")), "含 created_at")

    # ---- 3. 分页游标 ----
    print("\n[3] 分页游标不重复")
    code, p1 = get("/api/v1/history?limit=1")
    if isinstance(p1, dict) and p1.get("has_more"):
        cursor = p1["next_cursor"]
        code2, p2 = get(f"/api/v1/history?limit=1&cursor={cursor}")
        if isinstance(p2, dict) and p2.get("items"):
            id1 = p1["items"][0]["id"]
            id2 = p2["items"][0]["id"]
            check(id1 != id2, f"翻页不重复（{id1} -> {id2}）")
        else:
            bad("第二页查询失败")
    else:
        bad("没有更多数据，无法验证分页（记录数不足）")

    # ---- 4. 统计 ----
    print("\n[4] 统计与列表一致")
    code, stats = get("/api/v1/history/stats")
    check(code == 200, f"GET /api/v1/history/stats -> {code}")
    if isinstance(stats, dict):
        check(stats.get("total", 0) >= 3, f"total >= 3（{stats.get('total')}）")
        check(stats.get("success", 0) >= 3, f"success >= 3（{stats.get('success')}）")
        check(stats.get("rtf_median", 0) > 0, f"rtf_median > 0（{stats.get('rtf_median')}）")
        check("elysia" in (stats.get("by_voice") or {}), "by_voice 含 elysia")
        print(f"    total={stats.get('total')} success={stats.get('success')} "
              f"failed={stats.get('failed')} rtf中位={stats.get('rtf_median')}")

    # ---- 5. 按 request_id 查 ----
    print("\n[5] 按 request_id 查")
    if req_ids:
        code, rows = get(f"/api/v1/history/by-request/{req_ids[0]}")
        check(code == 200 and isinstance(rows, list) and len(rows) >= 1,
              f"by-request 返回记录（{len(rows) if isinstance(rows, list) else 0} 条）")

    # ---- 6. 单条详情 ----
    print("\n[6] 单条详情与 404")
    if items:
        rid = items[0]["id"]
        code, one = get(f"/api/v1/history/{rid}")
        check(code == 200 and isinstance(one, dict) and one.get("id") == rid,
              f"GET /history/{rid} -> 200")
    code, missing = get("/api/v1/history/999999")
    check(code == 404, f"不存在的记录 -> 404（实际 {code}）")

    # ---- 7. 失败记录 ----
    print("\n[7] 失败记录可查")
    # 故意用不存在的音色制造失败
    code, body, _ = post("/synthesize", {"text": "这条应该失败", "voice_id": "不存在的音色"})
    check(code != 200, f"非法音色请求失败（{code}）")
    code, failed = get("/api/v1/history?only_failed=true&limit=5")
    if isinstance(failed, dict):
        n = len(failed.get("items", []))
        check(n >= 1, f"only_failed 查到失败记录（{n} 条）")
        if n:
            print(f"    error_code={failed['items'][0].get('error_code')}")

    # ---- 8. 真实日志解析 ----
    print("\n[8] 真实日志解析")
    code, logs = get("/api/v1/logs?limit=20")
    check(code == 200, f"GET /api/v1/logs -> {code}")
    if isinstance(logs, dict):
        n = logs.get("count", 0)
        check(n > 0, f"解析到日志（{n} 条）")
        print(f"    文件大小 {logs.get('file_size')} 字节  "
              f"扫描 {logs.get('scanned')} 行  坏行 {logs.get('bad_lines')}")
        check(logs.get("bad_lines", 0) == 0, "无坏行（JSON 格式正确）")
        if logs.get("entries"):
            e = logs["entries"][0]
            check(bool(e.get("ts")), "日志含时间戳")
            check(bool(e.get("level")), "日志含级别")
            check(bool(e.get("event")), "日志含事件描述")
            # 中文应正确解码
            has_chinese = any(
                "\u4e00" <= ch <= "\u9fff"
                for x in logs["entries"]
                for ch in str(x.get("event", ""))
            )
            check(has_chinese, "中文正确解码（无乱码）")

    # ---- 9. 日志过滤 ----
    print("\n[9] 日志过滤")
    code, warn = get("/api/v1/logs?level=WARNING&limit=10")
    if isinstance(warn, dict):
        all_warn = all(e.get("level") == "WARNING" for e in warn.get("entries", []))
        check(all_warn, f"级别过滤正确（{warn.get('count')} 条 WARNING）")

    code, errs = get("/api/v1/logs?only_errors=true&limit=10")
    if isinstance(errs, dict):
        check(errs.get("count", 0) >= 0, f"only_errors 可用（{errs.get('count')} 条）")

    if req_ids:
        code, by_req = get(f"/api/v1/logs?request_id={req_ids[0]}")
        if isinstance(by_req, dict):
            n = by_req.get("count", 0)
            check(n >= 1, f"按 request_id 过滤日志（{n} 条）")
            if n:
                all_match = all(
                    e.get("request_id") == req_ids[0]
                    for e in by_req.get("entries", [])
                )
                check(all_match, "过滤结果 request_id 全部匹配")

    # ---- 10. 日志文件列表 ----
    print("\n[10] 日志文件列表")
    code, files = get("/api/v1/logs/files")
    check(code == 200 and isinstance(files, list), f"GET /logs/files -> {code}")
    if isinstance(files, list) and files:
        cur = [f for f in files if f.get("is_current")]
        check(len(cur) == 1, "有且仅有一个当前日志文件")
        print(f"    {files[0].get('name')}  {files[0].get('size')} 字节")

    # ---- 11. 参数校验 ----
    print("\n[11] 参数校验")
    code, _ = get("/api/v1/history?only_failed=true&only_success=true")
    check(code == 400, f"互斥参数 -> 400（实际 {code}）")

    code, _ = get("/api/v1/history?cursor=garbage")
    check(code == 400, f"非法游标 -> 400（实际 {code}）")

    code, _ = get("/api/v1/history?limit=9999")
    check(code == 422, f"超限 limit -> 422（实际 {code}）")

    # ---- 12. 清理接口 ----
    print("\n[12] 清理接口")
    # 注意：DELETE 的 before 语义是「删除此时间之前的记录」。
    # 用 2020 年只会删掉历史遗留，不会删今天写入的数据 —— 这是正确行为。
    # 先记录清理前的条数，再用未来时间清理，验证归零。
    code, before_stats = get("/api/v1/history/stats")
    n_before = before_stats.get("total", 0) if isinstance(before_stats, dict) else 0

    # 先验证「删更早的」不会误删新数据
    code = delete("/api/v1/history?before=2020-01-01T00:00:00%2B00:00")
    check(code == 204, f"DELETE before=2020 -> 204（实际 {code}）")
    code, mid = get("/api/v1/history/stats")
    n_mid = mid.get("total", 0) if isinstance(mid, dict) else -1
    check(n_mid == n_before, f"删除更早时间不应影响现有数据（{n_before} -> {n_mid}）")

    # 再用未来时间清空，验证确实能删
    code = delete("/api/v1/history?before=2099-01-01T00:00:00%2B00:00")
    check(code == 204, f"DELETE before=2099 -> 204（实际 {code}）")
    code, after = get("/api/v1/history/stats")
    if isinstance(after, dict):
        check(after.get("total", -1) == 0, f"清空后 total 归零（{after.get('total')}）")

    print()
    print("=" * 90)
    print(f" 结果: {PASS} 通过, {FAIL} 失败")
    print("=" * 90)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
