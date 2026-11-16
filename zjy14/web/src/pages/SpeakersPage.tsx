/** 声音管理：导入参考音频、查看质量报告、重新检测。 */

import { useCallback, useState } from "react";
import { describeError, speakerApi } from "../api/client";
import type { QualityIssue, QualityReport, Speaker } from "../api/types";
import {
  Alert,
  Badge,
  Card,
  Empty,
  ErrorBox,
  Loading,
  Metric,
  PageHead,
} from "../components/ui";
import { useAsync } from "../hooks/useAsync";

function levelBadge(level: QualityIssue["level"]) {
  if (level === "blocking") return <Badge kind="err">阻断</Badge>;
  if (level === "warning") return <Badge kind="warn">警告</Badge>;
  return <Badge kind="dim">提示</Badge>;
}

function QualityView({ r }: { r: Partial<QualityReport> }) {
  const issues = (r.issues ?? []) as QualityIssue[];
  const score = r.score ?? 0;
  return (
    <>
      <div className="grid grid-4" style={{ marginBottom: 12 }}>
        <Metric
          label="质量评分"
          value={
            <>
              {score.toFixed(2)}{" "}
              {r.ok ? <Badge kind="ok">可用</Badge> : <Badge kind="err">不可用</Badge>}
            </>
          }
        />
        <Metric label="时长" value={(r.duration_s ?? 0).toFixed(2)} unit="s" />
        <Metric label="信噪比" value={(r.snr_db ?? 0).toFixed(1)} unit="dB" />
        <Metric label="静音占比" value={((r.silence_ratio ?? 0) * 100).toFixed(1)} unit="%" />
      </div>
      <div className="hint" style={{ marginBottom: 10 }}>
        采样率 {r.sample_rate}Hz · {r.channels} 声道 · RMS {r.rms_dbfs}dBFS · 削波{" "}
        {((r.clip_ratio ?? 0) * 100).toFixed(3)}%
      </div>
      {issues.length > 0 && (
        <table>
          <thead>
            <tr>
              <th style={{ width: 70 }}>级别</th>
              <th style={{ width: 140 }}>代码</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {issues.map((i, idx) => (
              <tr key={idx}>
                <td>{levelBadge(i.level)}</td>
                <td className="mono">{i.code}</td>
                <td>{i.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

export default function SpeakersPage() {
  const list = useAsync(useCallback(() => speakerApi.list(), []));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [detail, setDetail] = useState<Speaker | null>(null);
  const [report, setReport] = useState<QualityReport | null>(null);

  const [form, setForm] = useState({
    speaker_id: "",
    display_name: "",
    audio_path: "",
    transcript: "",
    consent_status: "user_provided",
    source: "",
  });

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await speakerApi.create({
        speaker_id: form.speaker_id,
        display_name: form.display_name || form.speaker_id,
        audio_path: form.audio_path,
        transcript: form.transcript,
        consent_status: form.consent_status,
        source: form.source,
      });
      setShowForm(false);
      setForm({
        speaker_id: "",
        display_name: "",
        audio_path: "",
        transcript: "",
        consent_status: "user_provided",
        source: "",
      });
      list.reload();
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  const validate = async (id: string) => {
    setBusy(true);
    try {
      const r = await speakerApi.validate(id);
      setReport(r);
      list.reload();
    } catch (e) {
      alert(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    if (!confirm(`确定删除声音档案 ${id}？`)) return;
    try {
      await speakerApi.remove(id);
      if (detail?.speaker_id === id) setDetail(null);
      list.reload();
    } catch (e) {
      alert(describeError(e));
    }
  };

  return (
    <>
      <PageHead
        title="声音管理"
        desc="导入参考音频并做质量检测。参考文本必须与音频逐字对应，否则合成会失真。"
      />

      <div style={{ marginBottom: 16, display: "flex", gap: 8 }}>
        <button onClick={() => { setShowForm(true); setErr(null); }}>导入声音</button>
        <button className="ghost sm" onClick={list.reload}>
          刷新
        </button>
      </div>

      {list.error && <ErrorBox message={list.error} />}

      {showForm && (
        <Card
          title="导入参考音频"
          actions={
            <button className="ghost sm" onClick={() => setShowForm(false)}>
              取消
            </button>
          }
        >
          {err && <ErrorBox message={err} />}
          <div className="row">
            <div className="field">
              <label>声音 ID</label>
              <input
                value={form.speaker_id}
                onChange={(e) => setForm({ ...form, speaker_id: e.target.value })}
                placeholder="elysia"
              />
            </div>
            <div className="field">
              <label>显示名</label>
              <input
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              />
            </div>
          </div>
          <div className="field">
            <label>参考音频绝对路径（WSL 路径，如 /mnt/d/...）</label>
            <input
              value={form.audio_path}
              onChange={(e) => setForm({ ...form, audio_path: e.target.value })}
              placeholder="/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav"
              className="mono"
            />
          </div>
          <div className="field">
            <label>
              参考文本（<strong>必填</strong>，必须与音频逐字对应，含标点）
            </label>
            <textarea
              value={form.transcript}
              onChange={(e) => setForm({ ...form, transcript: e.target.value })}
              placeholder="我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡…"
            />
            <div className="hint" style={{ marginTop: 4 }}>
              提示：字数 ÷ 音频秒数 应接近 4–5 字/秒。偏离太多说明文本不全，
              合成会失真（实测会报 Kernel size 错误）。
            </div>
          </div>
          <div className="row">
            <div className="field">
              <label>授权状态</label>
              <select
                value={form.consent_status}
                onChange={(e) => setForm({ ...form, consent_status: e.target.value })}
              >
                <option value="user_provided">用户提供</option>
                <option value="licensed">已授权</option>
                <option value="unknown">未知</option>
              </select>
            </div>
            <div className="field">
              <label>来源说明</label>
              <input
                value={form.source}
                onChange={(e) => setForm({ ...form, source: e.target.value })}
                placeholder="素材来源描述"
              />
            </div>
          </div>
          <button onClick={submit} disabled={busy || !form.speaker_id || !form.audio_path || !form.transcript}>
            {busy ? "检测并导入中…" : "检测并导入"}
          </button>
        </Card>
      )}

      {report && (
        <Card
          title="质量检测报告"
          actions={
            <button className="ghost sm" onClick={() => setReport(null)}>
              关闭
            </button>
          }
        >
          <QualityView r={report} />
        </Card>
      )}

      <Card title={`声音档案（${list.data?.length ?? 0}）`}>
        {list.loading ? (
          <Loading />
        ) : !list.data || list.data.length === 0 ? (
          <Empty>还没有声音档案。点击「导入声音」开始。</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>时长</th>
                <th>格式</th>
                <th>质量</th>
                <th>缓存 key</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {list.data.map((s) => {
                const q = s.quality_report as Partial<QualityReport>;
                return (
                  <tr key={s.speaker_id}>
                    <td className="mono">{s.speaker_id}</td>
                    <td>{s.duration_s.toFixed(2)}s</td>
                    <td className="dim" style={{ fontSize: 12 }}>
                      {s.sample_rate}Hz / {s.channels}ch
                    </td>
                    <td>
                      {(q.score ?? 0).toFixed(2)}{" "}
                      {q.ok ? <Badge kind="ok">可用</Badge> : <Badge kind="warn">待检</Badge>}
                    </td>
                    <td className="mono dim" style={{ fontSize: 11 }}>
                      {s.feature_cache_key.slice(0, 8)}
                    </td>
                    <td>
                      <button className="ghost sm" onClick={() => setDetail(s)}>
                        详情
                      </button>{" "}
                      <button className="ghost sm" onClick={() => validate(s.speaker_id)} disabled={busy}>
                        重检
                      </button>{" "}
                      <button className="danger sm" onClick={() => remove(s.speaker_id)}>
                        删除
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      {detail && (
        <Card
          title={`声音详情：${detail.speaker_id}`}
          actions={
            <button className="ghost sm" onClick={() => setDetail(null)}>
              关闭
            </button>
          }
        >
          <div className="field">
            <label>参考文本</label>
            <pre>{detail.transcript}</pre>
          </div>
          <div className="hint" style={{ marginBottom: 12 }}>
            音频路径：<span className="mono">{detail.audio_path}</span>
            <br />
            模型版本：{detail.model_version} · 预处理版本：{detail.preprocessing_version}
            <br />
            授权：{detail.consent_status} · 来源：{detail.source || "—"}
          </div>
          <QualityView r={detail.quality_report as Partial<QualityReport>} />
        </Card>
      )}

      <Alert kind="warn">
        参考文本与音频不对齐是音色失真的头号原因。若合成报
        <span className="mono"> Kernel size can't be greater than actual input size</span>，
        先检查这里。
      </Alert>
    </>
  );
}
