/** 合成记录：查询历史合成、查看统计、按条件筛选。 */

import { useCallback, useEffect, useState } from "react";
import { describeError, historyApi } from "../api/client";
import type { HistoryPage, HistoryQuery, HistoryStats, SynthesisRecord } from "../api/types";
import {
  Alert,
  Badge,
  Card,
  ErrorBox,
  Metric,
  PageHead,
} from "../components/ui";

export default function HistoryPage() {
  const [page, setPage] = useState<HistoryPage | null>(null);
  const [stats, setStats] = useState<HistoryStats | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // 查询条件
  const [voiceId, setVoiceId] = useState("");
  const [emotion, setEmotion] = useState("");
  const [textContains, setTextContains] = useState("");
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [limit, setLimit] = useState(20);

  // 累积加载（游标分页）
  const [items, setItems] = useState<SynthesisRecord[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);

  const buildQuery = useCallback(
    (c?: string): HistoryQuery => ({
      voice_id: voiceId.trim() || undefined,
      emotion: emotion.trim() || undefined,
      text_contains: textContains.trim() || undefined,
      only_failed: onlyFailed || undefined,
      limit,
      cursor: c,
    }),
    [voiceId, emotion, textContains, onlyFailed, limit],
  );

  /** 首次查询 / 换条件：重置列表。 */
  const reload = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [p, s] = await Promise.all([
        historyApi.list(buildQuery()),
        historyApi.stats({
          voice_id: voiceId.trim() || undefined,
          emotion: emotion.trim() || undefined,
          text_contains: textContains.trim() || undefined,
          only_failed: onlyFailed || undefined,
        }),
      ]);
      setPage(p);
      setStats(s);
      setItems(p.items);
      setCursor(p.next_cursor);
      setHasMore(p.has_more);
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setLoading(false);
    }
  }, [buildQuery, voiceId, emotion, textContains, onlyFailed]);

  /** 加载更多：用游标追加。 */
  const loadMore = useCallback(async () => {
    if (!cursor || loading) return;
    setLoading(true);
    setErr(null);
    try {
      const p = await historyApi.list(buildQuery(cursor));
      setItems((prev) => [...prev, ...p.items]);
      setCursor(p.next_cursor);
      setHasMore(p.has_more);
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setLoading(false);
    }
  }, [cursor, loading, buildQuery]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <>
      <PageHead
        title="合成记录"
        desc="每次合成都会落库。可按音色、情绪标签、文本、成功/失败筛选，并用游标翻页。"
      />

      {err && <ErrorBox message={err} />}

      {stats && (
        <Card title="统计（不受分页影响）">
          <div className="grid grid-4" style={{ marginBottom: 12 }}>
            <Metric label="总记录" value={stats.total} />
            <Metric label="成功" value={stats.success} />
            <Metric label="失败" value={stats.failed} />
            <Metric
              label="成功率"
              value={(stats.success_rate * 100).toFixed(1)}
              unit="%"
            />
          </div>
          <div className="grid grid-4" style={{ marginBottom: 12 }}>
            <Metric label="音频总时长" value={stats.total_audio_s.toFixed(1)} unit="s" />
            <Metric label="合成总耗时" value={stats.total_synth_s.toFixed(1)} unit="s" />
            <Metric label="RTF 中位" value={stats.rtf_median.toFixed(3)} />
            <Metric
              label="RTF 范围"
              value={`${stats.rtf_min.toFixed(2)}–${stats.rtf_max.toFixed(2)}`}
            />
          </div>

          {Object.keys(stats.by_voice).length > 0 && (
            <div className="hint">
              按音色：
              {Object.entries(stats.by_voice).map(([k, v]) => (
                <span key={k} className="mono" style={{ marginRight: 12 }}>
                  {k}={v}
                </span>
              ))}
            </div>
          )}

          {Object.keys(stats.by_error).length > 0 && (
            <div className="hint" style={{ marginTop: 6 }}>
              按错误：
              {Object.entries(stats.by_error).map(([k, v]) => (
                <span key={k} className="mono" style={{ marginRight: 12 }}>
                  {k}={v}
                </span>
              ))}
            </div>
          )}

          {stats.first_at && (
            <div className="hint" style={{ marginTop: 6 }}>
              时间范围：{stats.first_at} → {stats.last_at}
            </div>
          )}
        </Card>
      )}

      <Card title="筛选条件">
        <div className="row">
          <div className="field">
            <label>音色</label>
            <input
              value={voiceId}
              onChange={(e) => setVoiceId(e.target.value)}
              placeholder="如 elysia（留空为全部）"
            />
          </div>
          <div className="field">
            <label>情绪标签</label>
            <input
              value={emotion}
              onChange={(e) => setEmotion(e.target.value)}
              placeholder="如 happy（仅元数据）"
            />
          </div>
          <div className="field">
            <label>文本包含</label>
            <input
              value={textContains}
              onChange={(e) => setTextContains(e.target.value)}
              placeholder="模糊匹配"
            />
          </div>
          <div className="field">
            <label>每页条数</label>
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {[10, 20, 50, 100].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 8 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={onlyFailed}
              onChange={(e) => setOnlyFailed(e.target.checked)}
            />
            只看失败的
          </label>
          <button onClick={() => void reload()} disabled={loading}>
            {loading ? "查询中…" : "查询"}
          </button>
        </div>
      </Card>

      <Card title={`记录列表（已加载 ${items.length} 条${page ? `，本页 ${page.count}` : ""}）`}>
        {items.length === 0 ? (
          <div className="empty">
            {loading ? "加载中…" : "没有匹配的记录。先去做一次合成测试。"}
          </div>
        ) : (
          <>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 60 }}>ID</th>
                  <th style={{ width: 110 }}>音色</th>
                  <th>文本</th>
                  <th style={{ width: 80 }}>时长</th>
                  <th style={{ width: 80 }}>耗时</th>
                  <th style={{ width: 70 }}>RTF</th>
                  <th style={{ width: 90 }}>状态</th>
                  <th style={{ width: 150 }}>时间</th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.id}</td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {r.voice_id || "—"}
                    </td>
                    <td style={{ fontSize: 12 }}>
                      {r.text.length > 40 ? `${r.text.slice(0, 40)}…` : r.text}
                    </td>
                    <td className="mono">{r.duration_s.toFixed(2)}s</td>
                    <td className="mono">{(r.total_ms / 1000).toFixed(2)}s</td>
                    <td className="mono">{r.rtf.toFixed(3)}</td>
                    <td>
                      {r.error_code ? (
                        <Badge kind="err">{r.error_code}</Badge>
                      ) : (
                        <Badge kind="ok">成功</Badge>
                      )}
                    </td>
                    <td className="mono" style={{ fontSize: 10 }}>
                      {r.created_at}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center" }}>
              <button onClick={() => void loadMore()} disabled={!hasMore || loading}>
                {loading ? "加载中…" : hasMore ? "加载更多" : "没有更多了"}
              </button>
              <span className="hint">
                游标分页：新记录写入时不会导致重复或跳过（对比页码分页）。
              </span>
            </div>
          </>
        )}
      </Card>

      <Alert kind="info">
        情绪标签（emotion）只是元数据，不参与合成 —— 已实测该模型不支持情绪控制，
        见 <span className="mono">docs/13_D1_EMOTION_VERDICT.md</span>。
        真正生效的是语速、音量、停顿与自然语言指令。
      </Alert>
    </>
  );
}
