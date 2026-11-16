"""流式合成策略：长短句路由 + 分块参数。

实测数据（2026-11-16，zjy14 当前环境，每组 3 次取中位数）：

  | 文本       | 字数 | 非流式首包 | 流式首包 | 改善    | 块数 |
  |-----------|-----|-----------|---------|--------|-----|
  | 短句       | 10  | 1.60s     | 1.67s   | -4.4%  | 2   |
  | 中句       | 22  | 3.11s     | 1.72s   | +44.8% | 3   |
  | 长句       | 44  | 5.36s     | 1.48s   | +72.4% | 4   |

结论：
  短句（< 15 字）流式无收益，反而因为要等 28 token 攒够一包而略慢。
  中长句流式收益显著，且越长越明显（长句首包从 5.36s 降到 1.48s）。

因此采用长短句路由：短句走非流式，中长句走流式。

为什么短句流式无收益（机制层面）：
  上游 cosyvoice/cli/model.py 的流式逻辑是「攒够 token_hop_len(25)
  + pre_lookahead_len(3) = 28 个 token 才出一包」。
  28 token 约等于 1.12 秒音频。短句总音频才 2 秒出头，
  等第一包出来时已经接近全部生成完，流式失去意义。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 流式路由阈值（字符数）。
#: 实测 10 字流式无收益（-4.4%），22 字有收益（+44.8%）。
#: 取中间值 15，留出安全边界。
STREAM_CHAR_THRESHOLD = 15

#: 上游流式的分块粒度（token 数）。
#: cosyvoice/cli/model.py 里 token_hop_len 初始值 25，
#: 加上 pre_lookahead_len 3，即每 28 token 出一包。
UPSTREAM_TOKEN_HOP_LEN = 25

#: 单块音频的目标时长（毫秒）。
#: 实测块大小 27840~96000 样本（1.16~4.0 秒），
#: 对 ESP32 播放端偏大。若需要更细的块，可在服务层二次切分。
TARGET_CHUNK_MS = 80


@dataclass(slots=True)
class StreamPolicy:
    """流式策略。"""

    #: 是否启用流式（全局开关）
    enabled: bool = True
    #: 路由阈值：字符数 >= 此值走流式
    threshold: int = STREAM_CHAR_THRESHOLD
    #: 服务层是否对上游块做二次切分（切成更小的块）
    reslice: bool = False
    #: 二次切分的目标块时长（毫秒）
    chunk_ms: int = TARGET_CHUNK_MS

    def should_stream(self, text: str) -> bool:
        """判断该文本是否应走流式。"""
        if not self.enabled:
            return False
        return count_chars(text) >= self.threshold

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "threshold": self.threshold,
            "reslice": self.reslice,
            "chunk_ms": self.chunk_ms,
        }


def count_chars(text: str) -> int:
    """统计有效字符数（不含标点与空白）。

    用有效字数而非总长度，因为标点不产生音频，
    不应对路由判断产生影响。
    """
    return len([c for c in (text or "") if c not in "，。！？、；：,.!?;: \n\t\r"])


def describe_routing(text: str, policy: StreamPolicy) -> dict:
    """返回路由决策的说明，供日志与 /system/status 展示。"""
    nc = count_chars(text)
    stream = policy.should_stream(text)
    return {
        "chars": nc,
        "threshold": policy.threshold,
        "mode": "stream" if stream else "oneshot",
        "reason": (
            f"字符数 {nc} >= 阈值 {policy.threshold}，走流式（实测首包收益 40%+）"
            if stream
            else f"字符数 {nc} < 阈值 {policy.threshold}，走非流式"
            "（短句流式无收益，实测 -4.4%）"
        ),
    }


__all__ = [
    "STREAM_CHAR_THRESHOLD",
    "TARGET_CHUNK_MS",
    "UPSTREAM_TOKEN_HOP_LEN",
    "StreamPolicy",
    "count_chars",
    "describe_routing",
]
