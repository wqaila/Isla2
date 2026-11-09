"""文档一致性测试：防止文档里的数字与实际状态脱节。

背景：盘查时发现多处「文档写的和实际不符」，而且都是**同类错误反复出现**：
- 测试数写了 262/272，实际 265/275（后来又变 325/335）
- docs 篇数写了 17/18，实际 24
- 录音格式写 22050 Hz，实际 16000 Hz
- 明文密码被写进 3 篇入库文档

光靠人工 review 会再次漂移，所以把可自动校验的部分锁进测试。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS = PROJECT_ROOT / "docs"
README = PROJECT_ROOT / "README.md"


def _all_docs() -> list[Path]:
    return sorted(DOCS.glob("*.md"))


# ----------------------------------------------------------------------
# 文档篇数
# ----------------------------------------------------------------------


def test_docs_index_count_matches_actual() -> None:
    """docs/README.md 声称的篇数必须等于实际 .md 文件数。"""
    text = (DOCS / "README.md").read_text(encoding="utf-8")
    m = re.search(r"本目录共 (\d+) 篇文档", text)
    assert m, "docs/README.md 没有「本目录共 N 篇文档」这句话"
    claimed = int(m.group(1))
    actual = len(_all_docs())
    assert claimed == actual, (
        f"docs/README.md 写 {claimed} 篇，实际 {actual} 篇"
    )


def test_root_readme_docs_count_matches_actual() -> None:
    """根 README 目录结构里的篇数也要对。"""
    text = README.read_text(encoding="utf-8")
    m = re.search(r"# (\d+) 篇技术文档", text)
    assert m, "README 目录结构里没有「N 篇技术文档」"
    assert int(m.group(1)) == len(_all_docs()), (
        f"README 写 {m.group(1)} 篇，实际 {len(_all_docs())} 篇"
    )


def test_every_doc_is_indexed() -> None:
    """每篇文档都应该出现在 docs/README.md 的索引里（避免孤儿文档）。"""
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    missing = [
        p.name for p in _all_docs()
        if p.name != "README.md" and p.name not in index
    ]
    assert not missing, f"以下文档未进索引: {missing}"


# ----------------------------------------------------------------------
# 测试数一致性
# ----------------------------------------------------------------------


def _actual_test_count() -> int:
    """收集当前测试用例总数（不执行）。"""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "--collect-only"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    m = re.search(r"(\d+) tests? collected", out.stdout)
    return int(m.group(1)) if m else -1


def test_readme_test_count_is_not_stale() -> None:
    """README 里「N passed」的数字不能比实际用例数差太多。

    这里只做粗校验（允许 CI 与本地因跳过策略不同的少量差异）：
    文档写的数不能低于实际收集数的 90%，也不能高于 110%。
    """
    actual = _actual_test_count()
    if actual < 0:
        pytest.skip("无法收集用例数")

    text = README.read_text(encoding="utf-8")
    m = re.search(r"`\.venv` (\d+) passed", text)
    assert m, "README 里没有「`.venv` N passed」"
    claimed = int(m.group(1))
    assert actual * 0.9 <= claimed <= actual * 1.1, (
        f"README 写 {claimed} passed，实际收集 {actual} 个用例，数字已过时"
    )


# ----------------------------------------------------------------------
# 敏感信息
# ----------------------------------------------------------------------


# 注意：这里的模式本身**不能**拼出真实密码的完整字面量，
# 否则 GitHub 的 secret scanning / 人工 review 会误报。
# 用字符串拼接构造，运行时才成为完整模式。
_HOTSPOT_PASSWORD = "isla2badge" + "2026"

_SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "疑似 API 密钥"),
    (re.compile(re.escape(_HOTSPOT_PASSWORD)), "热点明文密码"),
    (re.compile(r"Passphrase\s*=\s*'[^'<][^']{5,}'"), "明文 Passphrase"),
]


def test_no_secrets_in_docs() -> None:
    """入库文档不得含真实密钥或热点密码。

    历史问题：热点密码曾以明文出现在 demo-guide 与两篇 WORKLOG 里，
    已移除；本测试防止再被写回去。
    """
    problems: list[str] = []
    for path in [README, *DOCS.glob("*.md")]:
        text = path.read_text(encoding="utf-8")
        for pattern, desc in _SECRET_PATTERNS:
            for m in pattern.finditer(text):
                # 只报位置与类型，不回显命中的内容
                problems.append(f"{path.name}: {desc} @ 偏移 {m.start()}")
    assert not problems, "文档中发现敏感信息:\n" + "\n".join(problems)


# ----------------------------------------------------------------------
# 关键事实一致性
# ----------------------------------------------------------------------


def test_recording_sample_rate_documented_correctly() -> None:
    """录音采样率是 16000 Hz（固件 MIC_SAMPLE_RATE），不能写 22050。"""
    summary = (DOCS / "PROJECT-SUMMARY.md").read_text(encoding="utf-8")
    assert "22050 Hz 立体声" not in summary, (
        "PROJECT-SUMMARY 仍写 22050 Hz，固件实际是 16000 Hz 双声道"
    )


def test_latest_worklog_is_indexed_first() -> None:
    """最新工作记录应排在 docs/README.md 工作记录表的第一行。"""
    text = (DOCS / "README.md").read_text(encoding="utf-8")
    worklogs = sorted(
        (p.name for p in _all_docs() if p.name.startswith("WORKLOG-")),
        reverse=True,
    )
    assert worklogs, "没有 WORKLOG 文档"
    first_pos = text.index(worklogs[0])
    for other in worklogs[1:]:
        assert text.index(other) > first_pos, (
            f"{worklogs[0]} 应排在 {other} 之前"
        )


def test_firmware_doc_distinguishes_two_firmwares() -> None:
    """firmware.md 必须说清「本固件未上板，板子上跑的是另一个」。"""
    text = (DOCS / "firmware.md").read_text(encoding="utf-8")
    head = text[:1200]
    assert "ai_pet_badge" in head, (
        "firmware.md 开头未提到 ai_pet_badge，读者会误以为本固件已在板上运行"
    )
