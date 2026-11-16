/** 合成测试：参数调节 + 音频播放 + 元数据展示。 */

import { useCallback, useEffect, useRef, useState } from "react";
import { describeError, speakerApi, synthesisApi } from "../api/client";
import { PAUSE_PROFILES, type SynthesisMeta, type SynthesisRequest } from "../api/types";
import {
  Alert,
  Card,
  ErrorBox,
  Metric,
  PageHead,
} from "../components/ui";
import { useAsync } from "../hooks/useAsync";

interface Item {
  id: string;
  text: string;
  params: Record<string, unknown>;
  meta: SynthesisMeta;
  url: string;
}

export default function SynthesisPage() {
  const speakers = useAsync(useCallback(() => speakerApi.list(), []));

  const [text, setText] = useState("今天天气真不错，我们一起出去走走吧。");
  const [voiceId, setVoiceId] = useState("");
  const [speed, setSpeed] = useState(1.0);
  const [volume, setVolume] = useState(1.0);
  const [pauseProfile, setPauseProfile] = useState("natural");
  const [instruction, setInstruction] = useState("");

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const urls = useRef<string[]>([]);

  // 默认选第一个声音
  useEffect(() => {
    if (!voiceId && speakers.data && speakers.data.length > 0) {
      setVoiceId(speakers.data[0]!.speaker_id);
    }
  }, [speakers.data, voiceId]);

  // 卸载时释放 object URL，避免内存泄漏
  useEffect(() => {
    return () => {
      urls.current.forEach((u) => URL.revokeObjectURL(u));
    };
  }, []);

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      // 显式构造带类型的请求，避免 any 漏过字段错误
      const req: SynthesisRequest = {
        text,
        voice_id: voiceId || undefined,
        speed,
        volume,
        pause_profile: pauseProfile,
        instruction: instruction.trim() || null,
      };
      const { blob, meta } = await synthesisApi.synthesize(req);
      const url = URL.createObjectURL(blob);
      urls.current.push(url);
      setItems((prev) => [
        {
          id: `${Date.now()}`,
          text,
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
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead
        title="合成测试"
        desc="调节语速 / 音量 / 停顿，或输入自然语言指令。每次结果都会保留，便于 A/B 对比。"
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
              <div className="grid grid-4" style={{ marginBottom: 8 }}>
                <Metric label="时长" value={it.meta.duration.toFixed(2)} unit="s" />
                <Metric label="耗时" value={(it.meta.total_ms / 1000).toFixed(2)} unit="s" />
                <Metric label="RTF" value={it.meta.rtf.toFixed(3)} />
                <Metric label="采样率" value={it.meta.sample_rate} unit="Hz" />
              </div>
              <div className="audio-list">
                <audio controls src={it.url} />
              </div>
              <div className="hint" style={{ marginTop: 6 }}>
                文本：{it.text.slice(0, 60)}
                {it.text.length > 60 ? "…" : ""}
                <br />
                参数：
                <span className="mono">
                  speed={String(it.params.speed)} volume={String(it.params.volume)}{" "}
                  pause={String(it.params.pause_profile)}
                  {it.params.instruction ? ` instruction="${String(it.params.instruction)}"` : ""}
                </span>
                <br />
                request_id：<span className="mono">{it.meta.request_id}</span>
              </div>
            </div>
          ))
        )}
      </Card>

      <Alert kind="info">
        合成走非流式（Phase 4 才做流式）。长文本会等全部生成完才返回，
        首包延迟约等于总耗时。
      </Alert>
    </>
  );
}
