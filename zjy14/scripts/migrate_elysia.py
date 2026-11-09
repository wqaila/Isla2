"""把 zjy13 的角色卡与参考音频迁移进 zjy14。

只读 zjy13 的素材，不修改它。重复执行是幂等的（已存在则跳过）。

用法：
    wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && \\
      source ~/cosyvoice-venv/bin/activate && \\
      PYTHONPATH=. python -m scripts.migrate_elysia"
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from custom_voice.character.repository import Character, CharacterRepository
from custom_voice.db.migrate import migrate
from custom_voice.db.session import Database
from custom_voice.schemas.character import OFFICIAL_EMOTIONS
from custom_voice.speaker.cache import file_sha256, make_cache_key
from custom_voice.speaker.registry import Speaker, SpeakerRepository
from custom_voice.speaker.validate import analyze

ZJY13_DIR = Path("/mnt/d/xiangmudaima/zjy13")
VOICE_DIR = ZJY13_DIR / "server" / "characters" / "voices" / "elysia"
REF_WAV = VOICE_DIR / "ref_elysia.wav"
REF_TXT = VOICE_DIR / "ref_elysia.txt"
CARD_YAML = ZJY13_DIR / "server" / "characters" / "elysia.yaml"

SPEAKER_ID = "elysia"
CHARACTER_ID = "elysia"

#: 参考文本必须带此前缀，否则合成阶段会在卷积层报错。
#: 依据 zjy13/scripts/tts_server_wsl.py:39-46 的实测教训。
PROMPT_PREFIX = "You are a helpful assistant.<|endofprompt|>"


def _read_transcript() -> str:
    text = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" not in text:
        text = PROMPT_PREFIX + text
    return text


def _read_card() -> dict:
    if not CARD_YAML.is_file():
        return {}
    data = yaml.safe_load(CARD_YAML.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    missing = [p for p in (REF_WAV, REF_TXT) if not p.is_file()]
    if missing:
        print(f"[错误] 缺少素材: {missing}", file=sys.stderr)
        return 1

    db = Database()
    migrate(db)

    # ---- 声音档案 ----
    speaker_repo = SpeakerRepository(db)
    if speaker_repo.exists(SPEAKER_ID):
        print(f"[跳过] 声音档案 {SPEAKER_ID} 已存在")
    else:
        report = analyze(REF_WAV)
        print(f"[检测] {report.summary()}")
        for issue in report.issues:
            print(f"       [{issue.level}] {issue.code}: {issue.message}")

        if not report.ok:
            print("[错误] 参考音频质量不达标，中止导入", file=sys.stderr)
            return 2

        transcript = _read_transcript()
        model_version = "cosyvoice3-0.5b-2512"
        cache_key = make_cache_key(
            speaker_id=SPEAKER_ID,
            audio_sha256=file_sha256(REF_WAV),
            model_version=model_version,
            preprocessing_version="v1",
            prompt_text=transcript,
        )

        speaker_repo.create(
            Speaker(
                speaker_id=SPEAKER_ID,
                display_name="爱莉希雅",
                audio_path=str(REF_WAV),
                transcript=transcript,
                sample_rate=report.sample_rate,
                channels=report.channels,
                duration_s=report.duration_s,
                quality_report=report.to_dict(),
                consent_status="user_provided",
                source=str(VOICE_DIR),
                feature_cache_key=cache_key,
                model_version=model_version,
                preprocessing_version="v1",
            )
        )
        print(f"[创建] 声音档案 {SPEAKER_ID}  score={report.score}  key={cache_key[:8]}")

    # ---- 角色档案 ----
    char_repo = CharacterRepository(db)
    if char_repo.exists(CHARACTER_ID):
        print(f"[跳过] 角色 {CHARACTER_ID} 已存在")
    else:
        card = _read_card()
        # 把 zjy13 角色卡里能映射的字段搬过来。
        # 注意：zjy13 的 emotional_expression / speech_style 是给 LLM 用的自然语言，
        # 这里放进 personality / speaking_habits，不当作情绪控制参数。
        personality_parts = []
        for key in ("personality_traits", "values", "emotional_baseline"):
            val = card.get(key)
            if isinstance(val, list):
                personality_parts.extend(str(v) for v in val)
            elif val:
                personality_parts.append(str(val))

        char_repo.create(
            Character(
                character_id=CHARACTER_ID,
                display_name=card.get("name", "爱莉希雅"),
                description=str(card.get("identity", ""))[:2000],
                personality="；".join(personality_parts)[:2000],
                speaking_habits=str(card.get("speech_style", ""))[:2000],
                default_emotion="neutral",
                default_style="gentle",
                speaker_id=SPEAKER_ID,
                language="zh",
                # 决策 D4：3 条官方情绪为正式支持；comforting/playful 待 Phase 2 验证后加入
                allowed_emotions=list(OFFICIAL_EMOTIONS),
                voice_instruction_template="",
                model_id="Fun-CosyVoice3-0.5B-2512",
                model_version="cosyvoice3-0.5b-2512",
                config_version="v1",
            )
        )
        print(f"[创建] 角色 {CHARACTER_ID}  绑定声音 {SPEAKER_ID}")

    # ---- 汇总 ----
    print()
    print("=== 当前数据 ===")
    for s in speaker_repo.list_all():
        print(f"  声音 {s.speaker_id:<12} {s.duration_s:.1f}s  {s.transcript[:30]}...")
    for c in char_repo.list_all():
        print(
            f"  角色 {c.character_id:<12} {c.display_name}  "
            f"声音={c.speaker_id}  情绪={c.allowed_emotions}"
        )

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
