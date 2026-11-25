"""烧录前校验：确认已编译固件里真的含有本轮修复的关键代码特征。

为什么需要：源码改了但忘记重新 build，是这类项目最常见的坑
（WORKLOG-2026-11-23 里就记过「注释写 8192、实际参数还是 4096」）。
这里直接查二进制里的字符串常量 —— 只有被编译器真正保留的代码
才会留下这些字面量。

用法（在 zjy13 目录下）：
    .\\.venv\\Scripts\\python.exe scripts\\verify_fw_build.py

退出码 0 = 可以烧录；1 = 固件是旧的，先 idf.py build。
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = (
    HERE.parent.parent / "yuanma" / "AI-Pet-Badge-main"
    / "firmware" / "ai_pet_badge" / "build" / "ai_pet_badge.bin"
)

# (人类可读说明, 必须出现在二进制里的字符串片段)
MARKERS: list[tuple[str, str]] = [
    # 缺陷 1：收尾兜底提到预缓冲门之前（新增的告警文案）
    ("收尾兜底-结束标记", "结束标记已收到但 s_state 仍为 PLAYING"),
    ("收尾兜底-断流", "流式播放超时无数据且无结束标记，强制收尾"),
    # 缺陷 3：codec 写失败检查
    ("codec写失败检查", "写 codec 失败 ret="),
    ("codec连续失败收尾", "写 codec 连续失败"),
    # 缺陷 2：结束标记免轮次校验 + 丢帧留痕
    ("轮次不匹配留痕", "丢弃非本轮音频帧"),
    # 扬声器打开失败标记
    ("扬声器打开失败标记", "扬声器打开失败，本轮将无法出声"),
    # 缺陷 12（2026-11-25 二次定位）：set_fmt 关掉 TX 后必须补 enable
    ("写失败后丢弃不再重试", "写 codec 连续失败 50 次"),
]


def main() -> int:
    if not BIN.is_file():
        print(f"[失败] 固件不存在: {BIN}")
        return 1
    blob = BIN.read_bytes()
    print(f"=== 校验 {BIN.name}（{len(blob)} 字节）===")
    missing: list[str] = []
    for label, needle in MARKERS:
        hit = needle.encode("utf-8") in blob
        print(f"  {'[OK]  ' if hit else '[缺失]'} {label:22} {needle!r}")
        if not hit:
            missing.append(label)
    if missing:
        print(f"\n[失败] 缺少 {len(missing)} 项修复特征 —— 固件是旧的，请先 idf.py build")
        return 1
    print("\n[通过] 全部修复特征均在固件内，可以烧录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
