"""终端对话闭环：ASR（本地）+ LLM（云端）+ TTS（本地）。

这是 V2 阶段三的产物：不做 WebSocket、不做固件，只在终端验证整条链路。

架构：
    WAV 文件
      ↓ ASR（Windows .venv-asr，FunASR Paraformer，GPU）
    识别文本
      ↓ LLM（Windows .venv，DeepSeek 云端）
    回复文本
      ↓ TTS（WSL2，CosyVoice3，HTTP）
    WAV 音频

用法：
    # 单轮：给一个 WAV，跑完整链路
    python -m tools.dialogue_loop tests/audio/asr_example.wav

    # 多轮：连续对话，每次从固定音频或直接输入文本
    python -m tools.dialogue_loop --text-mode
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

from server.config import LLMConfig, PROJECT_ROOT, load_env_file  # noqa: E402
from server.dialogue.character import CharacterCard  # noqa: E402
from server.dialogue.session import ConversationSession  # noqa: E402
from server.llm import LLMClient, LLMError  # noqa: E402
from server.tts import TTSClient, TTSConfig, TTSError  # noqa: E402


@dataclass(slots=True)
class TurnTiming:
    """一轮对话的分段计时（对应 V2 第十五节的计时点）。"""

    t_record_end: float = 0.0
    t_asr_done: float = 0.0
    t_llm_first: float = 0.0
    t_llm_done: float = 0.0
    t_tts_first_audio: float = 0.0
    t_tts_done: float = 0.0

    def to_dict(self) -> dict[str, float]:
        def d(a: float, b: float) -> float:
            return round((b - a) * 1000, 1) if a and b else 0.0

        return {
            "asr_ms": d(self.t_record_end, self.t_asr_done),
            "llm_first_ms": d(self.t_asr_done, self.t_llm_first),
            "llm_total_ms": d(self.t_asr_done, self.t_llm_done),
            "tts_ms": d(self.t_llm_done, self.t_tts_done),
            "end_to_end_ms": d(self.t_record_end, self.t_tts_done),
        }


@dataclass(slots=True)
class DialogueResult:
    user_text: str
    reply_text: str
    audio_path: Path | None
    audio_duration_s: float
    timing: TurnTiming = field(default_factory=TurnTiming)


class ASRRunner:
    """在独立进程外复用 ASR 引擎（延迟导入，避免每次启动都加载模型）。"""

    def __init__(self, *, use_vad: bool = False) -> None:
        self.use_vad = use_vad
        self._engine = None

    def load(self) -> None:
        from asr.engine import ASREngine

        if self._engine is None:
            self._engine = ASREngine(use_vad=self.use_vad)
            self._engine.load()

    def transcribe(self, wav: str | Path) -> tuple[str, float]:
        """返回 (识别文本, 音频时长)。"""
        if self._engine is None:
            self.load()
        r = self._engine.transcribe_file(wav)
        return r.text, r.audio_duration_s


async def check_tts(cfg: TTSConfig) -> tuple[bool, dict]:
    async with TTSClient(cfg) as tts:
        return await tts.health()


async def run_turn(
    *,
    session: ConversationSession,
    tts_client: TTSClient,
    user_text: str,
    wav_path: Path | None,
    asr: ASRRunner | None,
    turn_no: int,
    voice_id: str | None = None,
) -> DialogueResult:
    """跑一轮完整链路。"""
    timing = TurnTiming()
    timing.t_record_end = time.perf_counter()

    # ---- ASR ----
    if wav_path is not None and asr is not None:
        print(f"  [ASR] 识别 {wav_path.name} ...", end="", flush=True)
        text, audio_dur = asr.transcribe(wav_path)
        timing.t_asr_done = time.perf_counter()
        print(f" {time.perf_counter()-timing.t_record_end:.2f}s")
        print(f"  识别: {text}")
        if not text.strip():
            print("  [跳过] 识别结果为空")
            return DialogueResult("", "", None, 0.0, timing)
        user_text = text
    else:
        timing.t_asr_done = timing.t_record_end

    # ---- LLM ----
    print(f"  [LLM] 生成回复 ...", end="", flush=True)
    first_at: list[float] = []

    def on_delta(_: str) -> None:
        if not first_at:
            first_at.append(time.perf_counter())

    try:
        record = await session.send(user_text, on_delta=on_delta)
    except LLMError as exc:
        print(f" 失败: {exc.code}: {exc.message}")
        return DialogueResult(user_text, "", None, 0.0, timing)

    timing.t_llm_first = first_at[0] if first_at else timing.t_asr_done
    timing.t_llm_done = time.perf_counter()
    reply = record.assistant_text
    llm_elapsed = timing.t_llm_done - timing.t_asr_done
    print(f" {llm_elapsed:.2f}s")
    print(f"  回复: {reply}")

    # ---- TTS ----
    if not reply.strip():
        return DialogueResult(user_text, reply, None, 0.0, timing)

    print(f"  [TTS] 合成语音 ...", end="", flush=True)
    try:
        tts_result = await tts_client.synthesize(
            reply,
            turn_id=f"tts-{turn_no}-{int(time.time()*1000)}",
            voice_id=voice_id,
        )
    except TTSError as exc:
        print(f" 失败: {exc.code}: {exc.message}")
        return DialogueResult(user_text, reply, None, 0.0, timing)

    timing.t_tts_done = time.perf_counter()
    tts_elapsed = timing.t_tts_done - timing.t_llm_done
    print(f" {tts_elapsed:.2f}s")
    print(f"  音频: {tts_result.audio_path}  ({tts_result.summary()})")

    return DialogueResult(
        user_text=user_text,
        reply_text=reply,
        audio_path=tts_result.audio_path,
        audio_duration_s=tts_result.duration_s,
        timing=timing,
    )


async def main_async(args: argparse.Namespace) -> int:
    load_env_file(PROJECT_ROOT / ".env")

    # ---- 配置检查 ----
    try:
        llm_cfg = LLMConfig.from_env()
        llm_cfg.validate()
    except Exception as exc:  # noqa: BLE001
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2

    tts_cfg = TTSConfig.from_env()
    # TTSConfig 是 frozen 的，用 replace 生成绝对路径版本
    out_dir = tts_cfg.output_dir
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    tts_cfg = replace(tts_cfg, output_dir=out_dir)

    # 角色 id 必须在 .env 加载之后再决定，否则读到的是进程启动时的旧环境变量。
    character_id = args.character or os.environ.get("DEFAULT_CHARACTER_ID", "default")
    card_path = PROJECT_ROOT / "server" / "characters" / f"{character_id}.yaml"
    if not card_path.is_file():
        print(f"[错误] 角色卡不存在: {card_path}", file=sys.stderr)
        print(f"  可选: {[p.stem for p in card_path.parent.glob('*.yaml')]}", file=sys.stderr)
        return 2

    card = CharacterCard.from_file(card_path)

    # 角色卡绑定的音色优先，其次才是环境变量里的默认音色。
    # 这样切换角色时声音跟着角色走，不需要额外改 .env。
    card_voice = (card.tts_settings() or {}).get("tts_voice_id")
    voice_id = card_voice or tts_cfg.default_voice

    print("=" * 60)
    print("  本地语音对话闭环")
    print("=" * 60)
    print(f"  角色  : {card.name} (v{card.prompt_version()})")
    print(f"  音色  : {voice_id}")
    print(f"  LLM   : {llm_cfg.model}")
    print(f"  TTS   : {tts_cfg.base_url}")

    # ---- TTS 健康检查（必须，不允许静默降级）----
    ok, info = await check_tts(tts_cfg)
    if not ok:
        print()
        print("[错误] 本地 TTS 服务不可用", file=sys.stderr)
        print(f"  {info}", file=sys.stderr)
        print()
        print("请先启动 TTS 服务：", file=sys.stderr)
        print("  wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/serve_tts_wsl.sh",
              file=sys.stderr)
        print()
        print("注意：按 V2 要求，TTS 不可用时不会自动回退到云端。", file=sys.stderr)
        return 2
    print(f"  TTS 状态: 就绪 (device={info.get('device')}, "
          f"gpu={info.get('gpu')}, 采样率={info.get('sample_rate')})")
    print()

    # ---- ASR 预加载 ----
    asr: ASRRunner | None = None
    if not args.text_mode:
        print("  [ASR] 加载模型 ...", end="", flush=True)
        try:
            asr = ASRRunner(use_vad=args.use_vad)
            asr.load()
            print(" 就绪")
        except Exception as exc:  # noqa: BLE001
            print(f" 失败: {exc}", file=sys.stderr)
            print("提示：ASR 需要 .venv-asr 环境，见 asr/README.md", file=sys.stderr)
            return 2
        print()

    results: list[DialogueResult] = []

    async with LLMClient(llm_cfg) as llm, TTSClient(tts_cfg) as tts_client:
        session = ConversationSession(card, llm)

        if args.text_mode:
            # 文本模式：直接输入文本，跳过 ASR（便于调试 LLM+TTS）
            print("文本模式（跳过 ASR）。输入 /quit 退出。\n")
            while True:
                try:
                    line = await asyncio.to_thread(input, "你 > ")
                except (EOFError, KeyboardInterrupt):
                    break
                line = line.strip()
                if not line:
                    continue
                if line in ("/quit", "/exit", "/q"):
                    break
                print()
                r = await run_turn(
                    session=session, tts_client=tts_client,
                    user_text=line, wav_path=None, asr=None,
                    turn_no=len(results) + 1,
                    voice_id=voice_id,
                )
                results.append(r)
                print()
        else:
            # 音频模式：跑指定 WAV
            wavs = [Path(args.audio)] if args.audio else sorted(
                (PROJECT_ROOT / "tests" / "audio").glob("*.wav")
            )
            if not wavs:
                print("[错误] 没有找到测试音频", file=sys.stderr)
                return 2

            for i, wav in enumerate(wavs, 1):
                print(f"--- 第 {i} 轮: {wav.name} ---")
                r = await run_turn(
                    session=session, tts_client=tts_client,
                    user_text="", wav_path=wav, asr=asr, turn_no=i,
                    voice_id=voice_id,
                )
                results.append(r)
                print()

    # ---- 汇总 ----
    if not results:
        return 0

    print("=" * 60)
    print("  计时汇总")
    print("=" * 60)
    header = f"{'轮次':<6}{'ASR':<10}{'LLM首字':<12}{'LLM完成':<12}{'TTS':<10}{'端到端':<12}"
    print(header)
    print("-" * len(header))
    for i, r in enumerate(results, 1):
        t = r.timing.to_dict()
        print(f"{i:<6}{t['asr_ms']:<10.0f}{t['llm_first_ms']:<12.0f}"
              f"{t['llm_total_ms']:<12.0f}{t['tts_ms']:<10.0f}{t['end_to_end_ms']:<12.0f}")

    ok_results = [r for r in results if r.audio_path]
    if ok_results:
        e2e = [r.timing.to_dict()["end_to_end_ms"] for r in ok_results]
        print()
        print(f"  成功 {len(ok_results)}/{len(results)} 轮")
        print(f"  端到端均值: {sum(e2e)/len(e2e):.0f} ms")
        print(f"  音频文件: {tts_cfg.output_dir}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="本地语音对话闭环（ASR+LLM+TTS）")
    ap.add_argument("audio", nargs="?", help="WAV 文件路径（音频模式）")
    ap.add_argument("--text-mode", action="store_true",
                    help="文本输入模式，跳过 ASR")
    ap.add_argument(
        "--character",
        default=None,
        help="角色卡 id（默认取 .env 的 DEFAULT_CHARACTER_ID）",
    )
    ap.add_argument("--use-vad", action="store_true", help="ASR 启用 VAD")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
