"""Phase 2b 测试：表达控制（路线乙）。

覆盖：
- 参数区间校验与归一化
- 自然语言指令解析
- 文本分句与停顿构造
- 音频增益与拼接
- 引擎集成（speed/volume/pause 生效，emotion 仅元数据）
"""

from __future__ import annotations

import numpy as np
import pytest

from custom_voice.expression.control import (
    EMOTION_LABELS,
    HARD_SPEED_MAX,
    HARD_SPEED_MIN,
    PAUSE_PROFILES,
    SAFE_SPEED_MAX,
    SAFE_SPEED_MIN,
    ExpressionParams,
    PauseProfile,
    build_params,
    clamp_speed,
    clamp_volume,
    normalize_emotion,
)
from custom_voice.expression.instruction import (
    merge_params,
    parse_instruction,
)
from custom_voice.expression.pause import (
    apply_gain,
    build_segments,
    concat_with_pauses,
    crossfade_concat,
    split_text,
)


# ----------------------------------------------------------------------
# 参数校验
# ----------------------------------------------------------------------


def test_default_params() -> None:
    p = build_params()
    assert p.speed == 1.0
    assert p.volume == 1.0
    assert p.pause_profile is PauseProfile.NATURAL
    assert p.emotion == "neutral"
    assert p.warnings == []


def test_speed_clamped_to_hard_range() -> None:
    """超出硬性区间必须被夹紧。"""
    p = build_params(speed=0.1)
    assert p.speed == HARD_SPEED_MIN
    p = build_params(speed=5.0)
    assert p.speed == HARD_SPEED_MAX


def test_speed_outside_safe_range_warns() -> None:
    """超出安全区间要警告（实测会导致音色漂移）。"""
    p = build_params(speed=0.6)
    assert p.speed == 0.6  # 未被夹紧，只警告
    assert any("超出建议区间" in w for w in p.warnings)


def test_speed_in_safe_range_no_warning() -> None:
    p = build_params(speed=1.1)
    assert p.warnings == []
    assert SAFE_SPEED_MIN <= p.speed <= SAFE_SPEED_MAX


def test_volume_clamped() -> None:
    p = build_params(volume=10.0)
    assert p.volume == 1.5
    assert any("超出安全区间" in w for w in p.warnings)


def test_pause_profile_enum() -> None:
    p = build_params(pause_profile="relaxed")
    assert p.pause_profile is PauseProfile.RELAXED


def test_pause_profile_unknown_falls_back() -> None:
    p = build_params(pause_profile="crazy")
    assert p.pause_profile is PauseProfile.NATURAL
    assert any("未知停顿档位" in w for w in p.warnings)


def test_emotion_unknown_falls_back() -> None:
    p = build_params(emotion="ecstatic")
    assert p.emotion == "neutral"
    assert any("未知情绪标签" in w for w in p.warnings)


def test_emotion_all_labels_accepted() -> None:
    for e in EMOTION_LABELS:
        p = build_params(emotion=e)
        assert p.emotion == e
        assert p.warnings == []


def test_intensity_clamped() -> None:
    assert build_params(intensity=-1.0).intensity == 0.0
    assert build_params(intensity=2.0).intensity == 1.0
    assert build_params(intensity=None).intensity == 0.5


def test_effective_controls_excludes_metadata() -> None:
    """生效参数与元数据必须明确分离。"""
    p = build_params(speed=0.9, volume=1.2, emotion="happy", style="gentle")
    eff = p.effective_controls()
    assert set(eff) == {"speed", "volume", "pause_ms"}
    assert "emotion" not in eff
    assert "style" not in eff

    meta = p.metadata_only()
    assert meta["emotion"] == "happy"
    assert "style" in meta
    assert "note" in meta


def test_pause_profiles_ordering() -> None:
    """停顿档位应递增。"""
    order = ["none", "tight", "natural", "relaxed", "dramatic"]
    vals = [PAUSE_PROFILES[k] for k in order]
    assert vals == sorted(vals)
    assert vals[0] == 0


def test_clamp_speed_returns_warning_tuple() -> None:
    v, w = clamp_speed(0.7)
    assert v == 0.7
    assert w is not None
    v, w = clamp_speed(1.0)
    assert w is None


def test_clamp_volume_returns_warning_tuple() -> None:
    v, w = clamp_volume(2.0)
    assert v == 1.5
    assert w is not None


def test_normalize_emotion() -> None:
    assert normalize_emotion(None) == "neutral"
    assert normalize_emotion("") == "neutral"
    assert normalize_emotion("HAPPY") == "happy"
    assert normalize_emotion("unknown") == "neutral"


# ----------------------------------------------------------------------
# 指令解析
# ----------------------------------------------------------------------


def test_parse_empty() -> None:
    r = parse_instruction("")
    assert r.params.speed == 1.0
    assert r.matched == {}


def test_parse_slow() -> None:
    r = parse_instruction("放慢速度")
    assert r.params.speed < 1.0
    assert "speed" in r.matched


def test_parse_fast() -> None:
    r = parse_instruction("快一点")
    assert r.params.speed > 1.0


def test_parse_longer_keyword_wins() -> None:
    """「慢一点」应优先于「慢」被匹配。"""
    r = parse_instruction("慢一点")
    assert r.matched.get("speed") == "慢一点"


def test_parse_volume() -> None:
    assert parse_instruction("大声一点").params.volume > 1.0
    assert parse_instruction("小声说话").params.volume < 1.0


def test_parse_pause() -> None:
    r = parse_instruction("多停顿一些")
    assert r.params.pause_profile is PauseProfile.RELAXED


def test_parse_emotion_metadata() -> None:
    """情绪词解析为元数据，不是模型指令。"""
    r = parse_instruction("像在安慰人")
    assert r.params.emotion == "comforting"
    # 关键：情绪不进 effective_controls
    assert "emotion" not in r.params.effective_controls()


def test_parse_combined() -> None:
    r = parse_instruction("温柔一点，放慢速度")
    assert r.params.emotion == "comforting"
    assert r.params.speed < 1.0
    assert len(r.matched) >= 2


def test_parse_unmatched_reports() -> None:
    """未识别内容要报告，不静默忽略。"""
    r = parse_instruction("用火星语说话")
    assert r.unmatched
    assert any("未识别" in w for w in r.params.warnings)


def test_parse_intensity_modifier() -> None:
    r = parse_instruction("非常开心")
    assert r.params.emotion == "happy"
    assert r.params.intensity is not None
    assert r.params.intensity > 0.5


def test_parse_english() -> None:
    r = parse_instruction("slow down please")
    assert r.params.speed < 1.0


# ----------------------------------------------------------------------
# 参数合并
# ----------------------------------------------------------------------


def test_merge_override_wins() -> None:
    base = build_params(speed=1.0, emotion="neutral", source="fields")
    over = build_params(speed=0.85, emotion="happy", source="instruction")
    m = merge_params(base, over)
    assert m.speed == 0.85
    assert m.emotion == "happy"


def test_merge_keeps_base_when_override_default() -> None:
    base = build_params(speed=0.9, volume=1.2, source="fields")
    over = build_params(source="instruction")  # 全默认
    m = merge_params(base, over)
    assert m.speed == 0.9
    assert m.volume == 1.2


def test_merge_accumulates_warnings() -> None:
    base = build_params(speed=0.6)  # 有警告
    over = build_params(volume=3.0)  # 有警告
    m = merge_params(base, over)
    assert len(m.warnings) >= 2


# ----------------------------------------------------------------------
# 文本分句
# ----------------------------------------------------------------------


def test_split_empty() -> None:
    assert split_text("") == []
    assert split_text("   ") == []


def test_split_sentences() -> None:
    segs = split_text("今天天气不错。我们出去玩吧。")
    assert len(segs) == 2
    assert segs[0].endswith("。")


def test_split_single_sentence() -> None:
    segs = split_text("今天天气不错")
    assert len(segs) == 1


def test_split_long_segment_by_comma() -> None:
    """超长句要按逗号再切。"""
    text = "一二三四五六七八九十，" * 8 + "结束。"
    segs = split_text(text, max_chars=30)
    assert len(segs) > 1
    assert all(len(s) <= 60 for s in segs)


def test_split_merges_short_fragments() -> None:
    """过短的片段应与相邻合并。"""
    segs = split_text("好的。嗯。今天天气真不错。", min_chars=4)
    # 「嗯。」应被合并，不会单独成段
    assert all(len(s) >= 4 or len(segs) == 1 for s in segs)


def test_build_segments_pause_zero_returns_single() -> None:
    segs = build_segments("今天天气不错。我们出去玩吧。", pause_ms=0)
    assert len(segs) == 1


def test_build_segments_with_pause() -> None:
    segs = build_segments("今天天气不错。我们出去玩吧。", pause_ms=200)
    assert len(segs) == 2
    assert segs[0].trailing_pause_ms == 200
    assert segs[-1].trailing_pause_ms == 0  # 最后一段无尾部静音


# ----------------------------------------------------------------------
# 音频处理
# ----------------------------------------------------------------------


def test_apply_gain_identity() -> None:
    x = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    out = apply_gain(x, 1.0)
    assert np.allclose(out, x)


def test_apply_gain_amplifies() -> None:
    x = np.full(100, 0.2, dtype=np.float32)
    out = apply_gain(x, 1.5)
    assert float(np.max(np.abs(out))) > 0.2


def test_apply_gain_no_clipping() -> None:
    """高增益下不能削波。"""
    x = np.full(1000, 0.9, dtype=np.float32)
    out = apply_gain(x, 1.5)
    assert float(np.max(np.abs(out))) <= 1.0


def test_apply_gain_attenuates() -> None:
    x = np.full(100, 0.8, dtype=np.float32)
    out = apply_gain(x, 0.5)
    assert float(np.max(np.abs(out))) < 0.5


def test_concat_with_pauses() -> None:
    a = np.ones(2400, dtype=np.float32) * 0.5
    b = np.ones(2400, dtype=np.float32) * 0.5
    out = concat_with_pauses([a, b], [100, 0], 24000)
    # 2400 + 2400（静音 100ms = 2400）+ 2400
    assert len(out) == 2400 + 2400 + 2400


def test_concat_single_part() -> None:
    a = np.ones(100, dtype=np.float32)
    out = concat_with_pauses([a], [0], 24000)
    assert len(out) == 100


def test_concat_empty() -> None:
    out = concat_with_pauses([], [], 24000)
    assert len(out) == 0


def test_crossfade_concat() -> None:
    a = np.ones(2400, dtype=np.float32) * 0.5
    b = np.ones(2400, dtype=np.float32) * 0.5
    out = crossfade_concat([a, b], 24000, fade_ms=8)
    # 重叠 8ms = 192 样本，总长 = 2400 + 2400 - 192 = 4608
    assert len(out) == 2400 + 2400 - 192


def test_crossfade_no_clipping_at_boundary() -> None:
    """交叉淡化区不能产生超过原信号的峰值。"""
    a = np.full(2400, 0.8, dtype=np.float32)
    b = np.full(2400, 0.8, dtype=np.float32)
    out = crossfade_concat([a, b], 24000, fade_ms=8)
    assert float(np.max(np.abs(out))) <= 0.8 + 1e-5


def test_crossfade_single() -> None:
    a = np.ones(100, dtype=np.float32)
    out = crossfade_concat([a], 24000)
    assert len(out) == 100


# ----------------------------------------------------------------------
# 引擎集成
# ----------------------------------------------------------------------


def test_engine_resolves_instruction(gpu_config):  # type: ignore[no-untyped-def]
    """引擎应把 instruction 解析成结构化参数。"""
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine
    from custom_voice.schemas.synthesis import SynthesisRequest

    cfg = gpu_config.model_copy(update={"engine": "cosyvoice3"})
    e = CosyVoiceEngine(cfg)
    req = SynthesisRequest(text="测试文本", instruction="放慢速度，温柔一点")
    expr = e._resolve_expression(req)
    assert expr.speed < 1.0
    assert expr.emotion == "comforting"


def test_engine_emotion_not_in_effective_controls(gpu_config):  # type: ignore[no-untyped-def]
    """emotion 不得进入引擎实际使用的参数。"""
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine
    from custom_voice.schemas.synthesis import SynthesisRequest

    cfg = gpu_config.model_copy(update={"engine": "cosyvoice3"})
    e = CosyVoiceEngine(cfg)
    req = SynthesisRequest(text="测试文本", emotion="happy", intensity=0.9)
    expr = e._resolve_expression(req)
    assert expr.emotion == "happy"  # 元数据保留
    assert "emotion" not in expr.effective_controls()  # 但不生效


def test_engine_instruction_overrides_fields(gpu_config):  # type: ignore[no-untyped-def]
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine
    from custom_voice.schemas.synthesis import SynthesisRequest

    cfg = gpu_config.model_copy(update={"engine": "cosyvoice3"})
    e = CosyVoiceEngine(cfg)
    # 显式 speed=1.0，指令要求放慢 -> 指令优先
    req = SynthesisRequest(text="测试", speed=1.0, instruction="慢一点")
    expr = e._resolve_expression(req)
    assert expr.speed < 1.0
