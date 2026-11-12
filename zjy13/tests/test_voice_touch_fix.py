"""长按说话录不到音（P0）修复的测试。

## 问题（2026-11-12 串口实录）

```
I (12118) badge: 长按说话 -> start=0
I (12272) badge: 松开发送              ← 只隔 154ms
E (12310) asr_failed: 音频过短（0.080s < 0.2s），拒绝识别
```

## 根因

LVGL 的事件模型与「按住说话」的直觉不匹配：

| 事件 | 触发时机 | 原用途 |
|---|---|---|
| `LV_EVENT_LONG_PRESSED` | **按住满 300ms 才触发一次** | 开始录音 |
| `LV_EVENT_RELEASED` | 松手时 | 停止录音 |

录音起点比手指按下晚 300ms，所以可用话音窗口 = 按住时长 − 300ms。
按 400ms 就松手 → 只录到约 100ms → 服务端拒绝。

## 修复（两条）

**方案 A（根治）**：改用 `LV_EVENT_PRESSED`（按下即录），
并用「按住时长 < 300ms 则取消录音、改切动画」保住短按功能。

**方案 B（兜底）**：`voice_talk_stop` 里加最短时长保护，
录到的时长 < 350ms 时本地取消并给服务端发 `cancel`，
不再把过短音频发上去让服务端报错。

本文件锁住这些约束，防止被改回去。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_C = (
    PROJECT_ROOT.parent / "yuanma" / "AI-Pet-Badge-main"
    / "firmware" / "ai_pet_badge" / "main" / "main.c"
)
VOICE_C = (
    PROJECT_ROOT.parent / "yuanma" / "AI-Pet-Badge-main"
    / "firmware" / "ai_pet_badge" / "main" / "voice_client.c"
)


def _read(p: Path) -> str:
    if not p.is_file():
        pytest.skip(f"固件源码不在预期位置: {p}")
    return p.read_text(encoding="utf-8", errors="replace")


# ----------------------------------------------------------------------
# 方案 A：按下即录音
# ----------------------------------------------------------------------


def test_registers_pressed_event() -> None:
    """必须注册 LV_EVENT_PRESSED 作为录音起点。"""
    src = _read(MAIN_C)
    assert "LV_EVENT_PRESSED" in src, (
        "没有注册 LV_EVENT_PRESSED；录音仍从长按阈值后才开始"
    )


def test_does_not_use_long_pressed_for_recording() -> None:
    """不能再把 LONG_PRESSED 当录音起点——它比按下晚 300ms。

    允许它出现在注释里（解释为什么弃用），但不能出现在
    ``lv_obj_add_event_cb`` 的实际注册中。
    """
    src = _read(MAIN_C)
    # 只看非注释行
    code_lines = [
        ln for ln in src.splitlines()
        if not ln.strip().startswith(("/*", "*", "//"))
    ]
    code = "\n".join(code_lines)
    assert "LV_EVENT_LONG_PRESSED" not in code, (
        "仍在注册 LV_EVENT_LONG_PRESSED，"
        "会导致录音窗口比按住时长少 300ms"
    )


def test_has_short_press_threshold() -> None:
    """必须有短按判定，否则短按切动画的功能会丢。"""
    src = _read(MAIN_C)
    assert "SHORT_PRESS_MS" in src, "缺少短按阈值常量"


def test_short_press_cancels_recording_and_switches_animation() -> None:
    """短按时要「取消录音 + 切动画」两件事都做。"""
    src = _read(MAIN_C)
    idx = src.index("static void on_voice_release")
    body = src[idx:idx + 1800]
    assert "voice_talk_stop" in body, "短按路径没有停止录音"
    assert "request_next_action" in body, "短按路径没有切动画"


def test_records_press_timestamp() -> None:
    """要记录按下时刻才能算按住时长。"""
    src = _read(MAIN_C)
    assert "s_press_tick" in src
    assert "xTaskGetTickCount" in src


def test_no_duplicate_click_handler() -> None:
    """不能再注册 CLICKED，否则短按会切两次动画。

    改造后短按逻辑在 RELEASED 里处理；若同时保留 CLICKED，
    LVGL 会在 release 之后再发一次 clicked，导致动画连跳两帧。
    """
    src = _read(MAIN_C)
    assert "LV_EVENT_CLICKED" not in src, (
        "仍注册了 LV_EVENT_CLICKED，短按会触发两次切动画"
    )


# ----------------------------------------------------------------------
# 方案 B：最短时长保护
# ----------------------------------------------------------------------


def test_has_min_talk_ms_constant() -> None:
    src = _read(VOICE_C)
    assert "MIN_TALK_MS" in src, "缺少最短录音时长常量"


def test_min_talk_ms_above_server_threshold() -> None:
    """固件阈值必须高于服务端下限（200ms），否则保护无效。"""
    src = _read(VOICE_C)
    m = re.search(r"#define\s+MIN_TALK_MS\s+(\d+)", src)
    assert m, "找不到 MIN_TALK_MS 定义"
    value = int(m.group(1))
    assert value >= 250, f"MIN_TALK_MS={value} 太小，仍可能录到无效音频"
    assert value <= 600, f"MIN_TALK_MS={value} 太大会误伤正常短句"


def test_short_recording_sends_cancel_not_stop() -> None:
    """过短录音必须发 cancel，不能发 recording_stop。

    发 stop 会让服务端去识别一段垃圾音频并报 asr_failed；
    发 cancel 则明确告知「本轮作废」，服务端不会白等。

    注意两点：
    1. C 源码里是转义形式 ``\\"cancel\\"``，不能按 ``"cancel"`` 找
    2. 不用大括号配对切函数体——注释里的 ``{`` ``}`` 会算错
    """
    src = _read(VOICE_C)
    fn_pos = src.index("esp_err_t voice_talk_stop")

    # C 字符串字面量里是 \"cancel\"，两种写法都接受
    cancel_pos = src.find('\\"cancel\\"', fn_pos)
    if cancel_pos < 0:
        cancel_pos = src.find('"cancel"', fn_pos)
    assert cancel_pos > 0, "voice_talk_stop 里没有发送 cancel 的代码"

    stop_pos = src.find('\\"recording_stop\\"', fn_pos)
    if stop_pos < 0:
        stop_pos = src.find('"recording_stop"', fn_pos)
    assert stop_pos > cancel_pos, (
        "cancel 分支位置不对：应在 recording_stop 之前"
    )

    # cancel 与 stop 之间必须有 return，证明会提前结束函数
    between = src[cancel_pos:stop_pos]
    assert "return" in between, (
        "cancel 分支没有提前 return，过短录音仍会走到 recording_stop"
    )


def test_records_start_timestamp_in_record_loop() -> None:
    """时间戳要在 record_loop 里记，不能用 talk_start 的时刻。

    两者差几十毫秒（等 session_start 才启动任务），
    用错会让时长判定偏乐观。
    """
    src = _read(VOICE_C)
    assert "s_record_start_us" in src
    idx = src.index("static void record_loop")
    body = src[idx:idx + 2500]
    assert "s_record_start_us = esp_timer_get_time()" in body, (
        "record_loop 里没有记录录音开始时刻"
    )


def test_includes_esp_timer() -> None:
    src = _read(VOICE_C)
    assert "#include \"esp_timer.h\"" in src, "缺少 esp_timer.h"


# ----------------------------------------------------------------------
# 服务端配套：必须能处理 cancel
# ----------------------------------------------------------------------


def test_server_handles_cancel() -> None:
    """固件会发 cancel，服务端必须正确处理并回 turn_cancelled。"""
    server_py = PROJECT_ROOT / "server" / "ws" / "server.py"
    src = server_py.read_text(encoding="utf-8")
    assert "CANCEL: self._on_cancel" in src
    assert "async def _on_cancel" in src
    assert "TURN_CANCELLED" in src
