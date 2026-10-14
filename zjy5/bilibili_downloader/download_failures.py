#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""下载失败清单 —— 记录失败的下载，支持 `--retry-failed` 精确补齐。

【为什么需要它】
批量下载 100 个视频、第 80 个挂了，现在只会在屏幕上打一行 `✗ 下载失败`
然后继续。重跑时**只有「文件存在就跳过」这一个信号** ——
失败清单与失败原因全部丢失，想补齐只能靠猜。

对比：`harvest` 早就有 `harvest_state.json` 断点续跑 + `--retry-failed`，
而下载器没有。本模块把这块补齐。

【与 harvest 状态文件的两个共同做法】
1. **原子写**：先写 `.tmp` 再 `os.replace`。状态文件写坏了比没有状态更糟 ——
   它会让下次运行误判。
2. **坏文件不炸**：读不动就按空清单继续。宁可丢清单，也不能中断下载。

【设计取舍：只记失败，不记成功】
成功的判据已经存在（文件在不在），再记一份是冗余；
而失败的判据**没有**，这才是缺口。
"""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

__all__ = ["FailureRecord", "FailureStore", "classify_failure", "FAILURE_KINDS"]

FAILURE_VERSION = 1

# 失败分类 → 中文说明。分类的意义是让用户知道**该不该重试**：
# 网络类重试有意义，Cookie 类重试多少次都一样。
FAILURE_KINDS = {
    "risk_control": "风控拦截（等一会儿再试，别硬撞）",
    "http_403": "403 拒绝（多半是 Cookie 失效或缺权限）",
    "not_found": "404（视频被删或 URL 错）",
    "ssl": "证书校验失败（可用 --no-check-certificate）",
    "network": "网络问题（重试有意义）",
    "unknown": "未知原因",
}

# 分类特征。顺序有讲究：**先匹配更具体的**，再落到泛化类别。
# ⚠️ `ssl` 必须排在 `risk_control` 前面：「证书校验失败」同时含
#    「证书」和「校验失败」两个特征，而 risk_control 的「校验失败」
#    更泛化 —— 顺序反了就会把 SSL 问题误报成风控，
#    用户会白等一轮而真正该做的是加 `--no-check-certificate`。
_KIND_PATTERNS = (
    ("ssl", ("certificate", "证书", "ssl", "tls")),
    ("http_403", ("403", "forbidden", "权限")),
    ("not_found", ("404", "not found", "不存在", "已删除", "失效")),
    ("risk_control", ("-352", "352", "风控", "拦截", "过于频繁")),
    ("network", ("timeout", "timed out", "超时", "connection", "连接",
                 "network", "网络", "reset", "refused")),
)


def classify_failure(reason: Any) -> str:
    """把失败原因文本归类，返回 `FAILURE_KINDS` 的键。

    分类的意义：`--retry-failed` 对「Cookie 过期」是无用的
    （重试多少次都一样），但对「网络超时」有用。
    不分类的话用户只能盲目重试。
    """
    if not reason:
        return "unknown"
    text = str(reason).lower()
    for kind, patterns in _KIND_PATTERNS:
        if any(p.lower() in text for p in patterns):
            return kind
    return "unknown"


@dataclass
class FailureRecord:
    """一条失败记录。"""

    url: str
    reason: str = ""
    title: str = ""
    at: str = ""
    attempts: int = 1
    kind: str = "unknown"

    def __post_init__(self):
        if not self.at:
            self.at = datetime.now().isoformat(timespec="seconds")
        if self.kind == "unknown":
            self.kind = classify_failure(self.reason)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FailureRecord":
        return cls(
            url=data.get("url", ""),
            reason=data.get("reason", ""),
            title=data.get("title", ""),
            at=data.get("at", ""),
            attempts=int(data.get("attempts", 1) or 1),
            kind=data.get("kind", "") or "unknown",
        )


class FailureStore:
    """失败清单的读写（JSON 落盘，原子替换）。

    :param path: 清单文件路径；为空时全部操作都是空操作
        （方便「不需要清单」的调用方直接传 None）
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.records: List[FailureRecord] = []
        self.load()

    def load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            # ⚠️ 坏文件不能让下载流程崩掉 —— 宁可丢清单也不能中断
            print(f"  ⚠️ 失败清单读取失败（按空清单继续）：{e}")
            return

        if isinstance(data, dict) and isinstance(data.get("records"), list):
            self.records = [FailureRecord.from_dict(r) for r in data["records"]
                            if isinstance(r, dict)]
        else:
            print("  ⚠️ 失败清单结构不认识，已忽略（不会覆盖原文件，除非有新失败）")

    def save(self) -> None:
        if not self.path:
            return
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        payload = {
            "version": FAILURE_VERSION,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "records": [r.to_dict() for r in self.records],
        }
        # 先写临时文件再替换：中途被打断也不会留下半个坏文件
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp_path, self.path)

    def record(self, url: str, reason: str = "", title: str = "",
               save: bool = True) -> FailureRecord:
        """记一条失败。同一 URL 只保留一条，原因更新、次数累加。"""
        for rec in self.records:
            if rec.url == url:
                rec.reason = reason or rec.reason
                rec.title = title or rec.title
                rec.attempts += 1
                rec.at = datetime.now().isoformat(timespec="seconds")
                rec.kind = classify_failure(rec.reason)
                if save:
                    self.save()
                return rec

        rec = FailureRecord(url=url, reason=reason, title=title)
        self.records.append(rec)
        if save:
            self.save()
        return rec

    def clear(self, url: str, save: bool = True) -> bool:
        """成功后移除该 URL。返回是否真的删掉了（便于调用方判断）。"""
        before = len(self.records)
        self.records = [r for r in self.records if r.url != url]
        removed = len(self.records) != before
        if removed and save:
            self.save()
        return removed

    def urls(self) -> List[str]:
        """导出待重试的 URL 列表。"""
        return [r.url for r in self.records]

    def summary(self) -> Dict[str, int]:
        """按分类统计，用于汇总打印。"""
        out: Dict[str, int] = {}
        for r in self.records:
            out[r.kind] = out.get(r.kind, 0) + 1
        return out

    def __len__(self) -> int:
        return len(self.records)
