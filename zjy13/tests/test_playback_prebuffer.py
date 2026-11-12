"""播放卡顿（首块）修复的测试。

## 问题（2026-11-12 用户反馈）

「更新固件后板子在刚开始播放回复的音频时会卡一下」

## 根因

流式 TTS 改造后，首块音频到达时板子缓冲里只有一小段。而固件的
``play_task`` 是「只要缓冲里有 1 个采样就立刻写 codec」：

```c
if (avail > 0 && s_spk) {
    esp_codec_dev_write(s_spk, out_buf, (int)(avail * 2));
    continue;          // 立刻回到循环顶部
}
// 缓冲空了
vTaskDelay(pdMS_TO_TICKS(10));
```

于是：写一小段 → 播空 → `vTaskDelay(10ms)` 等下一帧。
这 10ms 空档就是用户听到的「卡一下」。

非流式时代没这个问题，因为整段音频一次性到齐，缓冲永远不空。

## 修复（两端配合）

**固件**：加预缓冲 —— 开播前先攒够 ``PREBUF_MS``(120ms) 再写。
状态放静态变量 ``s_prebuf_done``，由 ``handle_playback_start`` 每轮复位。

**服务端**：首块不受 ``lead_sec`` 节流，一次发够 ``FIRST_BURST_MS``(300ms)，
否则板子会一直等不满预缓冲，反而更晚出声。

## 实测

```
playback_start 到达 : 1621 ms
累计到 120ms 音频   : 1621 ms   <- 同一时刻，预缓冲立即满足
累计到 300ms 音频   : 1622 ms
预缓冲就绪耗时      : 0 ms
```
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VOICE_C = (
    PROJECT_ROOT.parent / "yuanma" / "AI-Pet-Badge-main"
    / "firmware" / "ai_pet_badge" / "main" / "voice_client.c"
)
SERVER_PY = PROJECT_ROOT / "server" / "ws" / "server.py"


def _read(p: Path) -> str:
    if not p.is_file():
        pytest.skip(f"文件不存在: {p}")
    return p.read_text(encoding="utf-8", errors="replace")


# ----------------------------------------------------------------------
# 固件：预缓冲
# ----------------------------------------------------------------------


def test_has_prebuf_constant() -> None:
    src = _read(VOICE_C)
    assert "PREBUF_MS" in src, "缺少预缓冲常量"


def test_prebuf_value_reasonable() -> None:
    """预缓冲要在「挡住抖动」与「不增加延迟」之间取平衡。

    40ms 挡不住 10ms 的轮询空档；>300ms 会明显增加首字延迟。
    """
    src = _read(VOICE_C)
    m = re.search(r"#define\s+PREBUF_MS\s+(\d+)", src)
    assert m, "找不到 PREBUF_MS 定义"
    v = int(m.group(1))
    assert 60 <= v <= 300, f"PREBUF_MS={v} 不在合理区间 60~300"


def _play_task_body() -> str:
    """取 play_task 的函数体（跳过前置声明）。"""
    src = _read(VOICE_C)
    # 前置声明是 "static void play_task(void *arg);"
    # 定义是 "static void play_task(void *arg)\n{"
    m = re.search(r"static void play_task\(void \*arg\)\s*\n?\s*\{", src)
    assert m, "找不到 play_task 定义"
    start = m.start()
    # 用大括号配对找结尾（该函数体里没有字符串字面量含大括号，可安全配对）
    brace = src.index("{", start)
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail("play_task 函数体未闭合")


def test_prebuf_waits_before_first_write() -> None:
    """开播前必须真的等预缓冲，而不是立刻写。"""
    body = _play_task_body()
    assert "s_prebuf_done" in body, "play_task 没有预缓冲状态"
    assert "prebuf_need" in body, "没有计算预缓冲所需采样数"
    # 不够时要 continue 等下一轮，不能往下走去写 codec
    assert re.search(r"avail\s*<\s*prebuf_need", body), (
        "没有判断「缓冲是否够预缓冲」"
    )


def test_prebuf_state_reset_per_turn() -> None:
    """每轮播放必须复位预缓冲，否则第二轮起就失效。"""
    src = _read(VOICE_C)
    idx = src.index("static void handle_playback_start")
    body = src[idx:idx + 1500]
    assert "s_prebuf_done = false" in body, (
        "handle_playback_start 没有复位 s_prebuf_done，第二轮起预缓冲失效"
    )


def test_prebuf_does_not_deadlock_on_short_audio() -> None:
    """极短音频（总量不足预缓冲）不能死等。

    若音频只有 100ms，永远攒不到 120ms，必须靠结束标记放行。
    """
    body = _play_task_body()
    # 等待条件里应包含「已 draining」的放行分支
    assert re.search(r"avail\s*<\s*prebuf_need\s*&&\s*!\s*s_play_draining", body), (
        "预缓冲等待没有考虑 s_play_draining，极短音频会死等"
    )


def test_prebuf_resets_after_underrun() -> None:
    """中途播空后要重新攒，避免连续欠载时反复「写一小段→播空」。"""
    body = _play_task_body()
    assert "s_prebuf_done = false" in body, "播空后没有重置预缓冲"


# ----------------------------------------------------------------------
# 服务端：首块一次发够
# ----------------------------------------------------------------------


def test_server_has_first_burst() -> None:
    src = SERVER_PY.read_text(encoding="utf-8")
    assert "FIRST_BURST_MS" in src, "缺少首块突发常量"


def test_first_burst_value_covers_prebuf() -> None:
    """首块突发量必须大于固件预缓冲，否则板子等不满。

    固件 PREBUF_MS=120，服务端首块要给到 300ms 才有余量。
    """
    server_src = SERVER_PY.read_text(encoding="utf-8")
    m = re.search(r"FIRST_BURST_MS\s*=\s*(\d+)", server_src)
    assert m, "找不到 FIRST_BURST_MS"
    burst = int(m.group(1))

    voice_src = _read(VOICE_C)
    m2 = re.search(r"#define\s+PREBUF_MS\s+(\d+)", voice_src)
    if not m2:
        pytest.skip("固件缺少 PREBUF_MS")
    prebuf = int(m2.group(1))

    assert burst > prebuf, (
        f"服务端首块 {burst}ms 不大于固件预缓冲 {prebuf}ms，板子会等不满"
    )


def test_first_burst_bypasses_throttle() -> None:
    """首块阶段必须跳过 lead_sec 节流。"""
    src = SERVER_PY.read_text(encoding="utf-8")
    idx = src.index("FIRST_BURST_MS")
    # 找到下发循环
    loop_idx = src.index("for off in range(0, len(pcm_chunk), step)", idx)
    body = src[loop_idx:loop_idx + 900]
    assert "first_burst_bytes" in body, "下发循环没有使用首块阈值"
    assert re.search(r"if\s+sent_bytes\s*<\s*first_burst_bytes", body), (
        "没有按首块阈值分支"
    )
