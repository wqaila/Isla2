"""下行文本显示不完整 / 转义未解码的测试。

## 问题 1：日志里的「回复」被截断（不是 bug）

``server/ws/server.py`` 的日志行用的是 ``reply[:40]``：

```python
logger.info("LLM 完成 turn=%s 首字=%.0fms 总=%.0fms 回复=%r",
            my_turn, ..., reply[:40])
```

所以日志里看到的回复只有 40 字符，**这是日志截断，不是回复真的不完整**。
要确认完整内容得看 ``llm_final`` 下发的原文或 TTS 合成的音频。

## 问题 2：固件缓冲太小，真的会截断

| 消息 | 原缓冲 | 实际需要 |
|---|---|---|
| `llm_final` | `char[240]` | `LLM_MAX_TOKENS=128` ≈ 85 汉字 × 3 字节 ≈ **255 字节** |
| `asr_final` | `char[160]` | 长语音识别可达 60+ 汉字 ≈ **180+ 字节** |

两个都不够，屏幕与串口都会少尾巴。

## 问题 3：``json_get_str`` 不做转义解码

原实现逐字节原样拷贝：

```c
while (*v && !(*v == '"' && v[-1] != '\\') && o + 1 < cap) {
    out[o++] = *v++;
}
```

服务端发来的 ``\\n``、``\\"`` 会以字面量形式显示成两个字符，
而不是换行/引号。而 LLM 回复里确实常带 ``\\n``（历史日志可见）。

本文件锁住这三点的修复。
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
# 问题 1：日志截断是预期的，别误改成打印全文
# ----------------------------------------------------------------------


def test_log_truncates_reply_on_purpose() -> None:
    """日志应继续截断，避免刷屏；但注释要说清这是故意的。"""
    src = SERVER_PY.read_text(encoding="utf-8")
    idx = src.index("LLM 完成")
    line = src[idx:idx + 300]
    assert "reply[:" in line, (
        "日志里的回复截断被移除了；若确实要打全文，请先确认不会刷屏"
    )


# ----------------------------------------------------------------------
# 问题 2：固件文本缓冲必须够大
# ----------------------------------------------------------------------


def test_uses_shared_text_buffer() -> None:
    """下行文本应使用共享静态缓冲，而不是栈上小数组。

    原因：handle_text 跑在 WebSocket 事件任务栈上（仅 4096 字节），
    把栈数组加到 768 会明显加大栈压力。
    """
    src = _read(VOICE_C)
    assert "s_text_buf" in src, "没有共享文本缓冲"
    assert re.search(r"static\s+char\s+s_text_buf\[\d+\]", src), (
        "s_text_buf 不是静态数组"
    )


def test_text_buffer_large_enough() -> None:
    """缓冲要能装下 LLM_MAX_TOKENS=128 对应的汉字（约 255 字节）。

    留足余量：取 1024，可容纳约 340 汉字。
    """
    src = _read(VOICE_C)
    m = re.search(r"static\s+char\s+s_text_buf\[(\d+)\]", src)
    assert m, "找不到 s_text_buf 定义"
    size = int(m.group(1))
    assert size >= 512, (
        f"s_text_buf={size} 太小；85 汉字需 255 字节，建议 ≥512"
    )


def test_no_small_stack_text_arrays() -> None:
    """handle_text 里不应再用 160/240 这类小栈数组承载文本。

    注意排除注释：注释里保留 ``char text[240]`` 是为了说明历史原因，
    不算违规。
    """
    src = _read(VOICE_C)
    idx = src.index("static void handle_text")
    nxt = src.find("\nstatic ", idx + 10)
    body = src[idx:nxt] if nxt > 0 else src[idx:idx + 6000]

    # 去掉块注释与行注释
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)

    for small in ("char text[160]", "char text[240]"):
        assert small not in body, f"handle_text 里仍有 {small}，会截断长文本"

    # 应改用共享缓冲
    assert "s_text_buf" in body, "handle_text 没有使用共享文本缓冲"


# ----------------------------------------------------------------------
# 问题 3：json_get_str 必须解码转义
# ----------------------------------------------------------------------


def test_json_get_str_decodes_escapes() -> None:
    """必须处理 JSON 转义，否则 \\n 会以字面量显示。"""
    src = _read(VOICE_C)
    idx = src.index("bool json_get_str")
    nxt = src.find("\nstatic ", idx + 10)
    if nxt < 0:
        nxt = src.find("\nbool ", idx + 10)
    body = src[idx:nxt] if nxt > 0 else src[idx:idx + 4000]

    assert "case 'n':" in body, "没有处理 \\n 转义"
    assert "case 't':" in body, "没有处理 \\t 转义"
    assert "case 'u':" in body, "没有处理 \\uXXXX 转义"
    assert "0x80" in body or "0xE0" in body, "没有 UTF-8 编码逻辑"


def test_json_get_str_handles_unicode_escape() -> None:
    """\\uXXXX 要编码成 UTF-8，否则中文会乱码。"""
    src = _read(VOICE_C)
    idx = src.index("bool json_get_str")
    body = src[idx:idx + 4000]
    # UTF-8 三字节路径（中文都走这条）
    assert "0xE0" in body and "0x80" in body, (
        "\\uXXXX 没有按 UTF-8 编码输出"
    )


def test_json_get_str_guards_surrogate() -> None:
    """代理对（emoji）应转成替代字符，避免写出非法 UTF-8。"""
    src = _read(VOICE_C)
    idx = src.index("bool json_get_str")
    body = src[idx:idx + 4000]
    assert "0xD800" in body or "0xDFFF" in body or "0xFFFD" in body, (
        "没有处理代理对，emoji 可能产生非法 UTF-8"
    )


def test_json_get_str_respects_capacity() -> None:
    """关键容量约束：循环条件与多字节写出都要有 cap 保护。

    不按 ``out[o++]`` 出现次数机械计数——单字节路径由 while 条件
    ``o + 1 < cap`` 统一保护，多字节路径各自检查 ``o + N < cap``，
    这种结构本身是安全的。
    """
    src = _read(VOICE_C)
    idx = src.index("bool json_get_str")
    body = src[idx:idx + 4000]

    # 1) 主循环必须有边界条件
    assert re.search(r"while\s*\([^)]*o\s*\+\s*1\s*<\s*cap", body), (
        "主循环没有 o + 1 < cap 保护"
    )
    # 2) 多字节路径必须各自检查
    assert re.search(r"o\s*\+\s*2\s*<\s*cap", body), "2 字节路径没有容量检查"
    assert re.search(r"o\s*\+\s*3\s*<\s*cap", body), "3 字节路径没有容量检查"
    # 3) 结尾必须补 0
    assert re.search(r"out\[o\]\s*=\s*'\\0'", body), "没有补字符串结束符"


# ----------------------------------------------------------------------
# 一致性：固件缓冲与服务端上限
# ----------------------------------------------------------------------


def test_firmware_buffer_covers_server_text_limit() -> None:
    """固件缓冲要能覆盖服务端允许的常规回复长度。

    服务端 MAX_TEXT_CHARS=1000 是极端上限；实际 LLM_MAX_TOKENS=128
    约 85 汉字 ≈ 255 字节。固件 1024 字节足够覆盖实际使用。
    """
    from server.protocol import MAX_TEXT_CHARS

    src = _read(VOICE_C)
    m = re.search(r"static\s+char\s+s_text_buf\[(\d+)\]", src)
    assert m
    size = int(m.group(1))

    # 中文按 3 字节算，至少能装下 200 汉字
    assert size >= 200 * 3, (
        f"s_text_buf={size} 装不下 200 汉字（需 600 字节）"
    )
    # 但不必覆盖 MAX_TEXT_CHARS 这种极端值
    assert size <= MAX_TEXT_CHARS * 3, "缓冲没必要超过服务端上限"
