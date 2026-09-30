#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""批量字幕收割（harvest）

背景：`--subtitle-only` 早就实现了「直接拿 B 站 CC / AI 字幕」，但它一直是条**断头路**——
一次只能喂一个 URL，没人会手动贴几百个视频链接，于是产出的字幕也进不了语料链路。

这里把它接上：给一个 UP 主空间 URL（或一份 urls.txt 清单），自动枚举视频、
逐个探测字幕，命中就把文本抄下来 —— **不下载视频、不做 OCR、不做语音识别**。

设计要点
--------
1. **先探测、再取正文**：探测一次请求就能知道有没有字幕，命中率低时立刻能看出来，
   不用白跑一遍 OCR。
2. **断点续跑**：状态写在 ``state.json`` 里（按 BV 号记录结果）。
   中断后重跑会自动跳过已完成的；「无字幕」也记下来，避免每次都重复探测。
3. **只做加法**：本模块不改动下载、OCR、语音识别的任何既有逻辑。
"""

import json
import os
import re
import time
from collections import Counter
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

try:
    from .subtitle_extractor import SubtitleExtractor
    from .channel_crawler import ChannelCrawler
except ImportError:  # 直接以脚本方式运行本文件时回退
    from subtitle_extractor import SubtitleExtractor
    from channel_crawler import ChannelCrawler


BV_PATTERN = re.compile(r'BV[0-9A-Za-z]{10}')
STATE_VERSION = 1

# 状态取值（会写进 state.json）
STATUS_OK = "ok"                 # 有字幕且已收割
STATUS_PROBED = "probed"         # 只探测过「有字幕」，正文还没取 —— 正式收割时不能跳过
STATUS_NO_SUBTITLE = "no_subtitle"   # 探测过，确实没有字幕
STATUS_ERROR = "error"           # 请求/解析失败，值得重试
STATUS_SKIPPED = "skipped"       # 断点：上次已收割，本次跳过


def extract_bvid(text: str) -> Optional[str]:
    """从任意字符串里抠出 BV 号（没有则返回 None）。"""
    if not text:
        return None
    match = BV_PATTERN.search(text)
    return match.group(0) if match else None


def normalize_target(line: str) -> Optional[str]:
    """把清单里的一行整理成完整 URL。

    支持三种写法：完整 URL、裸 BV 号、``av`` 号会被原样保留在 URL 里。
    """
    if not line:
        return None
    text = line.strip()
    if not text or text.startswith("#"):
        return None

    if text.startswith("http://") or text.startswith("https://"):
        return text

    bvid = extract_bvid(text)
    if bvid:
        return f"https://www.bilibili.com/video/{bvid}"

    return None


def load_url_list(path: str) -> List[str]:
    """读取 urls.txt：一行一个 URL/ BV 号，``#`` 开头是注释，空行忽略。"""
    urls: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            url = normalize_target(raw)
            if url:
                urls.append(url)
    return urls


def looks_like_up_url(url: str) -> bool:
    """判断是不是 UP 主空间 URL。"""
    if not url:
        return False
    return bool(re.search(r'space\.bilibili\.com/\d+', url) or re.search(r'[?&]mid=\d+', url))


class HarvestState:
    """断点续跑的状态文件（json）。

    结构::

        {
          "version": 1,
          "updated_at": "...",
          "records": {
            "BV1xxxx": {"status": "ok", "sentences": 123, "lang": "zh-CN", "at": "..."}
          }
        }
    """

    def __init__(self, path: str):
        self.path = path
        self.records: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ⚠️ 状态文件读取失败（将当作空状态继续）：{e}")
            return

        if isinstance(data, dict) and isinstance(data.get("records"), dict):
            self.records = data["records"]
        else:
            print("  ⚠️ 状态文件结构不认识，已忽略（不会覆盖原文件，除非有新的收割结果）")

    def save(self) -> None:
        if not self.path:
            return
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        payload = {
            "version": STATE_VERSION,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "records": self.records,
        }
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        # 先写临时文件再替换：中途被打断也不会留下半个坏文件
        os.replace(tmp_path, self.path)

    def status_of(self, bvid: Optional[str]) -> Optional[str]:
        if not bvid:
            return None
        record = self.records.get(bvid)
        return record.get("status") if isinstance(record, dict) else None

    def mark(self, bvid: Optional[str], status: str, **extra: Any) -> None:
        if not bvid:
            return
        record = {"status": status, "at": datetime.now().isoformat(timespec="seconds")}
        record.update({k: v for k, v in extra.items() if v is not None})
        self.records[bvid] = record

    def should_skip(self, bvid: Optional[str], retry_failed: bool = False,
                    probe_only: bool = False) -> bool:
        """断点判断。

        - ``ok`` / ``no_subtitle``：已完成，两种模式都跳过
        - ``probed``：**只有探测模式才跳过**。
          探测（``--probe-only``）只是侦察，正文还没取；
          如果它把状态写成"已完成"，接着跑正式收割就会一个都不做 —— 那样
          「先探测再收割」这个推荐用法直接失效。
        - ``error``：默认跳过（避免反复撞同一堵墙），``--retry-failed`` 时重试
        """
        status = self.status_of(bvid)
        if status is None:
            return False
        if status == STATUS_ERROR:
            return not retry_failed
        if status == STATUS_PROBED:
            return bool(probe_only)
        return True

    def summary(self) -> Dict[str, int]:
        return dict(Counter(r.get("status", "unknown") for r in self.records.values()))


class SubtitleHarvester:
    """批量字幕收割器。

    :param extractor: 可注入（测试用）；默认自建 SubtitleExtractor
    :param crawler: 可注入（测试用）；默认自建 ChannelCrawler
    """

    def __init__(self, output_dir: str = "downloads", cookie_path: str = None,
                 verify_ssl: bool = True, state_path: str = None,
                 rate_limit: float = 1.0, out_format: str = "jsonl",
                 jsonl_name: str = "subtitles.jsonl",
                 extractor: SubtitleExtractor = None,
                 crawler: ChannelCrawler = None,
                 sleep_fn: Callable[[float], None] = time.sleep):
        self.output_dir = output_dir
        self.out_format = out_format
        self.jsonl_name = jsonl_name
        self.rate_limit = max(0.0, float(rate_limit or 0.0))
        self._sleep = sleep_fn

        self.extractor = extractor or SubtitleExtractor(
            cookie_path=cookie_path, output_dir=output_dir, verify_ssl=verify_ssl
        )
        self.crawler = crawler or ChannelCrawler(cookie_path, verify_ssl=verify_ssl)
        self.state = HarvestState(state_path) if state_path else HarvestState("")

        os.makedirs(output_dir, exist_ok=True)

    # ------------------------------------------------------------------ 目标收集

    def collect_up_videos(self, up_url: str, max_count: int = 0,
                          order: str = "pubdate") -> List[Dict[str, Any]]:
        """枚举 UP 主空间的视频（翻页），返回 ``[{"url","bvid","title"}]``。"""
        channel_id = self.crawler._get_channel_id(up_url)
        if not channel_id:
            print(f"  ✗ 无法从 URL 提取 UP 主 mid：{up_url}")
            return []

        print(f"正在枚举 UP 主 {channel_id} 的视频列表"
              f"（最多 {'不限' if not max_count else max_count} 个）...")

        def on_page(page: int, got: int, cumulative: List[Dict]) -> None:
            print(f"  第 {page} 页：+{got} -> 累计 {len(cumulative)}")

        videos = self.crawler.get_all_channel_videos(
            channel_id, max_count=max_count, order=order, progress_cb=on_page
        )

        targets = []
        for video in videos:
            url = video.get("url") or ""
            targets.append({
                "url": url,
                "bvid": extract_bvid(url) or video.get("video_id"),
                "title": video.get("title", ""),
            })
        print(f"共枚举到 {len(targets)} 个视频")
        return targets

    def collect_targets(self, target: str = None, urls_file: str = None,
                        max_count: int = 0, order: str = "pubdate") -> List[Dict[str, Any]]:
        """把「一个 URL / 一个 UP 空间 / 一份清单文件」统一成目标列表。"""
        targets: List[Dict[str, Any]] = []

        # 1) urls.txt 清单
        if urls_file:
            urls = load_url_list(urls_file)
            print(f"从清单 {urls_file} 读到 {len(urls)} 个 URL")
            targets.extend({"url": u, "bvid": extract_bvid(u), "title": ""} for u in urls)

        # 2) 位置参数：可能是 UP 空间、也可能是清单文件
        if target:
            if os.path.exists(target) and target.lower().endswith((".txt", ".list")):
                urls = load_url_list(target)
                print(f"从清单 {target} 读到 {len(urls)} 个 URL")
                targets.extend({"url": u, "bvid": extract_bvid(u), "title": ""} for u in urls)
            elif looks_like_up_url(target):
                targets.extend(self.collect_up_videos(target, max_count=max_count, order=order))
            else:
                url = normalize_target(target)
                if url:
                    targets.append({"url": url, "bvid": extract_bvid(url), "title": ""})
                else:
                    print(f"  ✗ 无法识别的目标：{target}")

        # 去重（同一个 BV 可能同时出现在清单和空间里）
        seen = set()
        unique = []
        for item in targets:
            key = item.get("bvid") or item.get("url")
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)

        if max_count and len(unique) > max_count:
            unique = unique[:max_count]
        return unique

    # ------------------------------------------------------------------ 收割

    def _write_video_txt(self, bvid: str, entries: List[Dict[str, Any]]) -> str:
        """txt 模式：一个视频一个文件，一行一句。"""
        dir_path = os.path.join(self.output_dir, "subtitles")
        os.makedirs(dir_path, exist_ok=True)
        path = os.path.join(dir_path, f"{bvid or 'unknown'}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(e["text"] for e in entries))
        return path

    def harvest_one(self, target: Dict[str, Any], probe_only: bool = False) -> Dict[str, Any]:
        """处理单个目标。返回 ``{"status":.., "sentences":.., ...}``。"""
        url = target.get("url") or ""
        bvid = target.get("bvid") or extract_bvid(url)

        probed = self.extractor.probe_subtitle(url)
        bvid = bvid or probed.get("bvid")

        if not probed.get("has_subtitle"):
            if probed.get("error"):
                return {"status": STATUS_ERROR, "error": probed["error"], "bvid": bvid}
            return {"status": STATUS_NO_SUBTITLE, "bvid": bvid}

        info = {
            "lang": probed.get("lang"),
            "ai_type": probed.get("ai_type"),
        }

        if probe_only:
            return {"status": STATUS_PROBED, "bvid": bvid, "sentences": None, **info}

        entries, meta = self.extractor.fetch_subtitle_entries(
            video_url=url, subtitle_url=probed.get("subtitle_url"), info=probed
        )
        if meta.get("error"):
            return {"status": STATUS_ERROR, "error": meta["error"], "bvid": bvid, **info}
        if not entries:
            return {"status": STATUS_NO_SUBTITLE, "bvid": bvid, **info}

        if self.out_format == "txt":
            path = self._write_video_txt(bvid, entries)
        else:
            path = os.path.join(self.output_dir, self.jsonl_name)
            # B 站字幕接口不给置信度，这里如实写 null，不要编一个数字出来
            SubtitleExtractor.write_entries_jsonl(
                entries, path, video=bvid, channel="subtitle",
                confidence=None, lang=info["lang"], ai_type=info["ai_type"],
            )

        return {
            "status": STATUS_OK,
            "bvid": bvid,
            "sentences": len(entries),
            "output": path,
            **info,
        }

    def harvest_targets(self, targets: List[Dict[str, Any]],
                        probe_only: bool = False,
                        retry_failed: bool = False) -> Dict[str, int]:
        """逐个收割。断点逻辑在这里生效。"""
        stats = Counter()
        total = len(targets)
        if not total:
            print("没有可处理的目标")
            return dict(stats)

        for index, target in enumerate(targets, 1):
            bvid = target.get("bvid") or extract_bvid(target.get("url"))
            title = (target.get("title") or "")[:28]

            if self.state.should_skip(bvid, retry_failed=retry_failed, probe_only=probe_only):
                stats[STATUS_SKIPPED] += 1
                prev = self.state.records.get(bvid, {}).get("status")
                print(f"[{index}/{total}] {bvid or target.get('url')} 已完成（{prev}），跳过")
                continue

            label = f"[{index}/{total}] {bvid or target.get('url')}"
            if title:
                label += f"  {title}"

            try:
                result = self.harvest_one(target, probe_only=probe_only)
            except Exception as e:                      # 单个视频失败不中断整批
                result = {"status": STATUS_ERROR, "error": f"{type(e).__name__}: {e}"}

            status = result.get("status")
            stats[status] += 1

            if status in (STATUS_OK, STATUS_PROBED):
                if probe_only:
                    print(f"{label}  ✓ 有字幕（{result.get('lang') or '未知语言'}）")
                else:
                    print(f"{label}  ✓ {result.get('sentences')} 句"
                          f"（{result.get('lang') or '未知语言'}）→ {result.get('output')}")
            elif status == STATUS_NO_SUBTITLE:
                print(f"{label}  – 无字幕")
            else:
                print(f"{label}  ✗ 失败：{result.get('error')}")

            # 探测本身也要一个请求，无字幕的视频不值得为它多睡一轮
            self.state.mark(
                result.get("bvid") or bvid, status,
                sentences=result.get("sentences"),
                lang=result.get("lang"),
                error=result.get("error"),
            )
            self.state.save()

            if status == STATUS_OK and not probe_only and self.rate_limit:
                self._sleep(self.rate_limit)

        return dict(stats)

    def run(self, target: str = None, urls_file: str = None, max_count: int = 0,
            order: str = "pubdate", probe_only: bool = False,
            retry_failed: bool = False) -> Dict[str, int]:
        """入口：收集目标 -> 逐个收割 -> 打印汇总。"""
        targets = self.collect_targets(target, urls_file=urls_file,
                                       max_count=max_count, order=order)
        if not targets:
            return {}

        if probe_only:
            print(f"\n开始探测（只问有没有字幕，不取正文），共 {len(targets)} 个视频")
        else:
            print(f"\n开始收割，共 {len(targets)} 个视频")

        stats = self.harvest_targets(targets, probe_only=probe_only,
                                    retry_failed=retry_failed)

        handled = sum(stats.get(k, 0) for k in (STATUS_OK, STATUS_PROBED, STATUS_NO_SUBTITLE,
                                                STATUS_ERROR))
        hit = stats.get(STATUS_OK, 0) + stats.get(STATUS_PROBED, 0)
        print("\n" + "=" * 52)
        print("收割汇总" if not probe_only else "探测汇总")
        print("=" * 52)
        print(f"  本次处理   : {handled}")
        print(f"  命中字幕   : {hit}")
        print(f"  无字幕     : {stats.get(STATUS_NO_SUBTITLE, 0)}")
        print(f"  失败       : {stats.get(STATUS_ERROR, 0)}")
        print(f"  断点跳过   : {stats.get(STATUS_SKIPPED, 0)}")
        if handled:
            print(f"  命中率     : {hit / handled * 100:.1f}%")
            if hit == 0:
                print("  → 一个都没命中：这个 UP 可能没开 AI 字幕。"
                      "别急着上 OCR，先换个 UP 试。")
            elif hit / handled < 0.3:
                print("  → 命中率偏低：OCR / 语音识别仍有必要，但可以先收完这一批。")
            else:
                print("  → 命中率不错：剩下没字幕的那几个再走 OCR / 语音识别就够。")
        if probe_only and hit:
            print("  → 正式收割时这些视频仍会重新取正文（探测只记「有字幕」，不算已完成）")
        if not probe_only and hit:
            if self.out_format == "txt":
                print(f"  输出目录   : {os.path.join(self.output_dir, 'subtitles')}")
            else:
                print(f"  输出文件   : {os.path.join(self.output_dir, self.jsonl_name)}")

        state_summary = self.state.summary()
        if state_summary:
            print(f"  状态文件累计: {state_summary}")
        return stats


def harvest(target: str = None, urls_file: str = None, output_dir: str = "downloads",
            cookie_path: str = None, state_path: str = None, max_count: int = 0,
            order: str = "pubdate", out_format: str = "jsonl",
            rate_limit: float = 1.0, probe_only: bool = False,
            retry_failed: bool = False, verify_ssl: bool = True) -> Dict[str, int]:
    """便捷函数：一行调用完成批量收割。"""
    harvester = SubtitleHarvester(
        output_dir=output_dir, cookie_path=cookie_path, verify_ssl=verify_ssl,
        state_path=state_path, rate_limit=rate_limit, out_format=out_format,
    )
    return harvester.run(target=target, urls_file=urls_file, max_count=max_count,
                         order=order, probe_only=probe_only, retry_failed=retry_failed)
