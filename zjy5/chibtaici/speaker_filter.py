#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""轻量「说话人分离」—— 用本地 LLM 给每句台词打「像不像角色本人」的分。

为什么不用 pyannote 那类说话人日志（diarization）：它能分出「有几个人、谁在说」，
但要装一堆重依赖、还得跑聚类，对「只要角色本人台词」这个目标来说太重。
而语料纯度的核心缺口其实只是「这句是不是角色说的」，用本地模型批量打标就够：

- 每句给 1~5 分（5=非常像，1=明显是别人/旁白/歌词）
- 低分剔除，边界分可以放行（宁多勿漏）
- 几千句也就十几分钟

只有标准库依赖；Ollama 不在也不报错，只提示并放弃打分（**绝不静默丢数据**）。

> ⚠️ 访问本机 Ollama 必须**绕开系统代理**。否则代理会把 127.0.0.1 的请求也接走，
> 表现为莫名其妙的 502（zjy9 上踩过同样的坑）。
"""

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["LlmSpeakerFilter", "DEFAULT_ROLE", "DEFAULT_MODEL", "build_prompt"]

DEFAULT_ROLE = "爱莉希雅"
# ⚠️ 这个默认值必须指向**本机确实装了的**模型。
# 原来写的是 `qwen3:8b`，但本机从未安装它 —— 而本模块的失败模式是
# 「提示一句、跳过打分、句子全部保留」（设计如此：宁可不打分也不丢数据），
# 所以**跑完不会报错**，只会发现结果里没有 `speaker_score`。
# 本机装的是 `qwen3.5:9b-gguf`（5.7GB，100% 进显存，96 tok/s）。
DEFAULT_MODEL = "qwen3.5:9b-gguf"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"

_PROMPT_TEMPLATE = """你是动漫台词筛选助手。下面是某段视频里识别出的台词，已编号。
请逐句判断它是否像「{role}」说的（语气、用词、口癖、人设是否吻合）。

评分标准：
5 = 非常像 {role}
4 = 像 {role}
3 = 不确定（信息太少）
2 = 不太像，更像别人
1 = 明显是其他角色 / 旁白 / 歌词 / 广告

只输出一个 JSON 数组，不要任何解释，格式：
[{{"i": 1, "score": 5}}, {{"i": 2, "score": 2}}]

台词列表：
{lines}
"""


def build_prompt(role: str, texts: Sequence[str]) -> str:
    """把一批台词拼成一条打标 prompt（带编号）。"""
    lines = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1))
    return _PROMPT_TEMPLATE.format(role=role, lines=lines)


class LlmSpeakerFilter:
    """用本地 Ollama 给句子打「角色相似度」分并剔除低分句。"""

    def __init__(self, role: str = DEFAULT_ROLE, model: str = DEFAULT_MODEL,
                 base_url: str = DEFAULT_BASE_URL, threshold: int = 3,
                 batch_size: int = 20, timeout: int = 120,
                 max_failures: int = 3, opener=None):
        """
        :param threshold: 低于这个分就剔除（默认 3：只清掉「不太像」和「明显不是」）
        :param batch_size: 一次请求打标多少句
        :param max_failures: 连续失败多少次后放弃（避免几千句全在报错里泡着）
        :param opener: 可注入（测试用）
        """
        self.role = role
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.threshold = int(threshold)
        self.batch_size = max(1, int(batch_size))
        self.timeout = int(timeout)
        self.max_failures = max(1, int(max_failures))
        self._opener = opener or self._build_opener()

    @staticmethod
    def _build_opener():
        """绕开系统代理：本机请求不该经过代理。"""
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ------------------------------------------------------------- 与 Ollama 交互

    def check_available(self) -> bool:
        """探测 Ollama 是否在跑、模型是否存在。"""
        try:
            with self._opener.open(f"{self.base_url}/api/tags", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"  ⚠️ 连不上本地 Ollama（{self.base_url}）：{e}")
            return False

        names = [m.get("name", "") for m in data.get("models", [])]
        if not names:
            print("  ⚠️ Ollama 里没有任何模型，先 `ollama pull` 一个再来打标")
            return False
        if self.model not in names and f"{self.model}:latest" not in names:
            # 名称不完全相等也可能可用（比如带 tag 的写法），提示但不阻断
            print(f"  ⚠️ 没在 Ollama 里看到 {self.model}，现有：{', '.join(names[:6])}"
                  f"{' …' if len(names) > 6 else ''}")
        return True

    def _ask(self, texts: Sequence[str]) -> Optional[List[Optional[int]]]:
        """问一次模型，返回与 texts 等长的分数列表；失败返回 None。"""
        payload = {
            "model": self.model,
            "prompt": build_prompt(self.role, texts),
            "stream": False,
            # ⚠️ 必须显式关掉思考模式：Qwen3.5 默认会把 token 全花在
            #    "Thinking Process:" 上 —— 实测 20 句一批要 27 秒（本该 2 秒），
            #    而且思考文本混进输出会让 JSON 解析失败。
            #    注意：prompt 里加 /no_think 是**无效**的，只有这个参数管用。
            "think": False,
            "options": {
                "temperature": 0,
                # 每句只要一个分数（约 8 token），留 3 倍余量足够
                "num_predict": max(64, len(texts) * 24),
            },
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

        return self._parse_scores(body.get("response", ""), len(texts))

    @staticmethod
    def _parse_scores(raw: str, expected: int) -> Optional[List[Optional[int]]]:
        """从模型回复里抠出分数。模型爱夹带解释，所以先试整体 JSON 再退到正则。"""
        scores: List[Optional[int]] = [None] * expected

        text = (raw or "").strip()
        candidates: List[Any] = []
        try:
            candidates.append(json.loads(text))
        except json.JSONDecodeError:
            start, end = text.find("["), text.rfind("]")
            if start != -1 and end > start:
                try:
                    candidates.append(json.loads(text[start:end + 1]))
                except json.JSONDecodeError:
                    pass

        for data in candidates:
            if isinstance(data, dict):
                data = data.get("results") or data.get("scores") or []
            if not isinstance(data, list):
                continue
            filled = False
            for item in data:
                if not isinstance(item, dict):
                    continue
                index = item.get("i", item.get("index"))
                score = item.get("score")
                if not isinstance(index, int) or not isinstance(score, (int, float)):
                    continue
                if 1 <= index <= expected:
                    scores[index - 1] = int(max(1, min(5, score)))
                    filled = True
            if filled:
                return scores

        # 最后兜底：按出现顺序抓 {"i":n,...,"score":m}
        pairs = re.findall(r'"i"\s*:\s*(\d+)[^}]*?"score"\s*:\s*(\d)', text)
        if pairs:
            for index, score in pairs:
                position = int(index) - 1
                if 0 <= position < expected:
                    scores[position] = int(max(1, min(5, int(score))))
            return scores

        return None

    def score_texts(self, texts: Sequence[str]) -> Tuple[List[Optional[int]], Dict[str, int]]:
        """批量打分。返回 ``(分数列表, 统计)``。"""
        stats: Dict[str, int] = {"scored": 0, "unscored": 0, "batches": 0, "failures": 0}
        scores: List[Optional[int]] = []
        failures = 0

        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start:start + self.batch_size])
            batch_scores = None

            if failures < self.max_failures:
                batch_scores = self._ask(batch)
                stats["batches"] += 1

            if batch_scores is None:
                failures += 1
                stats["failures"] += 1
                if failures == self.max_failures:
                    print(f"  ⚠️ 打标连续失败 {self.max_failures} 次，已中止打分"
                          f"（剩余 {len(texts) - start} 句按「未打分」保留，不做剔除）")
                batch_scores = [None] * len(batch)

            for score in batch_scores:
                scores.append(score)
                stats["scored" if score is not None else "unscored"] += 1

        return scores, stats

    # ------------------------------------------------------------- 对条目操作

    def filter_entries(self, entries: List[Dict[str, Any]],
                       text_key: str = "text") -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """给条目打标并剔除低分句。

        :return: ``(保留的条目, 统计)``；未打分的句子**保留**（宁多勿漏）
        """
        stats: Dict[str, int] = {"total": len(entries), "kept": 0, "dropped": 0,
                                 "unscored": 0, "scored": 0, "failures": 0}
        if not entries:
            return [], stats

        if not self.check_available():
            print("  → 已跳过语气打标（数据一行都没丢）")
            stats["kept"] = len(entries)
            stats["unscored"] = len(entries)
            return [dict(e) for e in entries], stats

        texts = [str(e.get(text_key, "")) for e in entries]
        scores, score_stats = self.score_texts(texts)
        stats["failures"] = score_stats.get("failures", 0)

        kept: List[Dict[str, Any]] = []
        for entry, score in zip(entries, scores):
            record = dict(entry)
            record["speaker_score"] = score
            if score is None:
                stats["unscored"] += 1
            else:
                stats["scored"] += 1
            if score is not None and score < self.threshold:
                stats["dropped"] += 1
                continue
            stats["kept"] += 1
            kept.append(record)

        print(f"语气打标：已评 {stats['scored']} 句、未评 {stats['unscored']} 句，"
              f"剔除低于 {self.threshold} 分的 {stats['dropped']} 句"
              f"（保留 {stats['kept']}/{stats['total']}）")
        return kept, stats
