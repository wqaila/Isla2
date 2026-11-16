/** 合成测试：参数调节 + 流式/非流式播放 + 元数据对比。
 *
 * 流式模式的意义（实测数据来自 Phase 4）：
 *   长句 44 字：非流式首包 5.36s vs 流式 1.48s（改善 72.4%）
 *   中句 22 字：非流式 3.11s vs 流式 1.72s（改善 44.8%）
 *   短句 10 字：非流式 1.60s vs 流式 1.67s（无收益，-4.4%）
 * 所以短句会自动走非流式（后端 StreamPolicy 路由），
 * 但本页仍让用户能手动对比两种模式的真实差异。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { describeError, speakerApi, synthesisApi } from "../api/client";
import type { StreamSummary } from "../api/client";
import {
  PAUSE_PROFILES,
  type SynthesisMeta,
  type SynthesisRequest,
} from "../api/types";
import { StreamPlayer } from "../audio/StreamPlayer";
import {
  Alert,
  Badge,
  Card,
  ErrorBox,
  Metric,
  PageHead,
} from "../components/ui";
import { useAsync } from "../hooks/useAsync";

type Mode = "stream" | "oneshot";

interface Item {
  id: string;
  text: string;
  mode: Mode;
  params: Record<string, unknown>;
  /** 非流式才有 */
  meta?: SynthesisMeta;
  /** 流式才有 */
  stream?: StreamSummary;
  /** 非流式才有的可播放 URL */
  url?: string;
  /** 流式播放的实测首声延迟 */
  firstSoundMs?: number;
}

export default function SynthesisPage() {
  const speakers = useAsync(useCallback(() => speakerApi.list(), []));

  const [text, setText] = useState("今天天气真不错，我们一起出去走走吧。");
  const [voiceId, setVoiceId] = useState("");
  const [speed, setSpeed] = useState(1.0);
  const [volume, setVolume] = useState(1.0);
  const [pauseProfile, setPauseProfile] = useState("natural");
  const [instruction, setInstruction] = useState("");
  const [mode, setMode] = useState<Mode>("stream");

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const urls = useRef<string[]>([]);
  const playerRef = useRef<StreamPlayer | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 默认选第一个声音
  useEffect(() => {
    if (!voiceId && speakers.data && speakers.data.length > 0) {
      setVoiceId(speakers.data[0]!.speaker_id);
    }
  }, [speakers.data, voiceId]);

  // 卸载时释放资源
  useEffect(() => {
    return () => {
      urls.current.forEach((u) => URL.revokeObjectURL(u));
      playerRef.current?.stop();
      abortRef.current?.abort();
    };
  }, []);

  const buildRequest = (): SynthesisRequest => ({
    text,
    voice_id: voiceId || undefined,
    speed,
    volume,
    pause_profile: pauseProfile,
    instruction: instruction.trim() || null,
  });

  const runOneshot = async (req: SynthesisRequest) => {
    const { blob, meta } = await synthesisApi.synthesize(req);
    const url = URL.createObjectURL(blob);
    urls.current.push(url);
    setItems((prev) => [
      {
        id: `${Date.now()}-o`,
        text,
        mode: "oneshot",
        params: {
          speed: req.speed,
          volume: req.volume,
          pause_profile: req.pause_profile,
          instruction: req.instruction,
        },
        meta,
        url,
      },
      ...prev,
    ]);
  };

  const runStream = async (req: SynthesisRequest) => {
    playerRef.current?.stop();
    const player = new StreamPlayer(24000);
    player.start();
    playerRef.current = player;

    const ac = new AbortController();
    abortRef.current = ac;

    const summary = await synthesisApi.synthesizeStream(
      req,
      (pcm) => player.push(pcm),
      ac.signal,
    );

    const stats = player.snapshot();
    setItems((prev) => [
      {
        id: `${Date.now()}-s`,
        text,
        mode: "stream",
        params: {
          speed: req.speed,
          volume: req.volume,
          pause_profile: req.pause_profile,
          instruction: req.instruction,
        },
        stream: summary,
        firstSoundMs: stats.firstChunkMs ?? undefined,
      },
      ...prev,
    ]);
  };

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      const req = buildRequest();
      if (mode === "stream") {
        await runStream(req);
      } else {
        await runOneshot(req);
      }
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  };

  const cancel = () => {
    abortRef.current?.abort();
    playerRef.current?.stop();
    setBusy(false);
  };

  return (
    <>
      <PageHead
        title="合成测试"
        desc="调节语速 / 音量 / 停顿，或输入自然语言指令。支持流式与非流式对比。"
      />

      {err && <ErrorBox message={err} />}

      <Card title="合成参数">
        <div className="field">
          <label>待合成文本</label>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            placeholder="输入要合成的文本…"
          />
          <div className="hint" style={{ marginTop: 4 }}>
            {text.length} 字符
          </div>
        </div>

        <div className="row">
          <div className="field">
            <label>音色</label>
            <select value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
              {(speakers.data ?? []).length === 0 && <option value="">（无可用音色）</option>}
              {(speakers.data ?? []).map((s) => (
                <option key={s.speaker_id} value={s.speaker_id}>
                  {s.speaker_id}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>语速 {speed.toFixed(2)}×（安全区间 0.85–1.15）</label>
            <input
              type="range"
              min={0.5}
              max={2.0}
              step={0.05}
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
            />
          </div>
          <div className="field">
            <label>音量 {volume.toFixed(2)}×</label>
            <input
              type="range"
              min={0.5}
              max={1.5}
              step={0.05}
              value={volume}
              onChange={(e) => setVolume(Number(e.target.value))}
            />
          </div>
          <div className="field">
            <label>停顿档位</label>
            <select value={pauseProfile} onChange={(e) => setPauseProfile(e.target.value)}>
              {PAUSE_PROFILES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}（{p.ms}ms）
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="field">
          <label>自然语言指令（可选，会覆盖上面的结构化参数）</label>
          <input
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder="例：温柔一点，放慢速度，多停顿"
          />
          <div className="hint" style={{ marginTop: 4 }}>
            支持：慢/快、大声/小声、多停顿/紧凑、安慰/开心 等关键词。
          </div>
        </div>

        <div className="field">
          <label>合成模式</label>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input
                type="radio"
                checked={mode === "stream"}
                onChange={() => setMode("stream")}
              />
              流式（边收边播，首声更快）
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input
                type="radio"
                checked={mode === "oneshot"}
                onChange={() => setMode("oneshot")}
              />
              非流式（等全部生成完）
            </label>
          </div>
          <div className="hint" style={{ marginTop: 4 }}>
            实测：长句 44 字流式首声 1.48s vs 非流式 5.36s（快 72%）；
            短句 10 字两者接近（流式反而慢 4%）。后端会自动按 15 字阈值路由。
          </div>
        </div>

        <div style={{ display: "flex", gap: 10 }}>
          <button onClick={run} disabled={busy || !text.trim() || !voiceId}>
            {busy ? (
              <>
                <span className="spinner" />
                合成中…
              </>
            ) : (
              "开始合成"
            )}
          </button>
          {busy && (
            <button className="ghost" onClick={cancel}>
              取消
            </button>
          )}
        </div>
      </Card>

      <Card title={`合成历史（${items.length}）`}>
        {items.length === 0 ? (
          <div className="empty">还没有合成结果。点击「开始合成」。</div>
        ) : (
          items.map((it) => (
            <div
              key={it.id}
              style={{
                borderBottom: "1px solid var(--border)",
                paddingBottom: 14,
                marginBottom: 14,
              }}
            >
              <div style={{ marginBottom: 8 }}>
                <Badge kind={it.mode === "stream" ? "ok" : "dim"}>
                  {it.mode === "stream" ? "流式" : "非流式"}
                </Badge>
              </div>

              {it.mode === "stream" && it.stream ? (
                <>
                  <div className="grid grid-4" style={{ marginBottom: 8 }}>
                    <Metric
                      label="首声延迟"
                      value={(it.firstSoundMs ?? it.stream.firstChunkMs).toFixed(0)}
                      unit="ms"
                    />
                    <Metric label="音频时长" value={it.stream.durationS.toFixed(2)} unit="s" />
                    <Metric label="总耗时" value={(it.stream.totalMs / 1000).toFixed(2)} unit="s" />
                    <Metric label="分块数" value={it.stream.chunks} />
                  </div>
                  <div className="hint">
                    首声远小于总耗时，说明音频在生成过程中就开始播放了。
                    共 {it.stream.totalBytes} 字节 / {it.stream.sampleRate}Hz。
                  </div>
                </>
              ) : it.meta ? (
                <>
                  <div className="grid grid-4" style={{ marginBottom: 8 }}>
                    <Metric label="时长" value={it.meta.duration.toFixed(2)} unit="s" />
                    <Metric label="耗时" value={(it.meta.total_ms / 1000).toFixed(2)} unit="s" />
                    <Metric label="RTF" value={it.meta.rtf.toFixed(3)} />
                    <Metric label="采样率" value={it.meta.sample_rate} unit="Hz" />
                  </div>
                  {it.url && (
                    <div className="audio-list">
                      <audio controls src={it.url} />
                    </div>
                  )}
                </>
              ) : null}

              <div className="hint" style={{ marginTop: 6 }}>
                文本：{it.text.slice(0, 60)}
                {it.text.length > 60 ? "…" : ""}
                <br />
                参数：
                <span className="mono">
                  speed={String(it.params.speed)} volume={String(it.params.volume)}{" "}
                  pause={String(it.params.pause_profile)}
                  {it.params.instruction
                    ? ` instruction="${String(it.params.instruction)}"`
                    : ""}
                </span>
                <br />
                request_id：
                <span className="mono">
                  {it.stream?.requestId || it.meta?.request_id || "—"}
                </span>
              </div>
            </div>
          ))
        )}
      </Card>

      <Alert kind="info">
        流式模式下音频边收边播，不做本地缓存（刷新后不可重播）。
        若需要可重播的音频，请用非流式模式。
        <br />
        情绪标签不参与合成 —— 已实测该模型不支持情绪控制，
        请让 LLM 在文本里表达情绪（如「嘿嘿~」「唔…」）。
      </Alert>
    </>
  );
}
