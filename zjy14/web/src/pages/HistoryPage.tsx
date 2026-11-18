/** 合成记录：查询历史合成、查看统计、按条件筛选。 */

import { useCallback, useEffect, useState } from "react";
import { describeError, historyApi } from "../api/client";
import type {
  CleanupRecord,
  HistoryPage,
  HistoryQuery,
  HistoryStats,
  SynthesisRecord,
} from "../api/types";
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
  const [cleanup, setCleanup] = useState<CleanupRecord[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);

  // 查询条件
  const [voiceId, setVoiceId] = useState("");
  const [emotion, setEmotion] = useState("");
  const [textSearch, setTextSearch] = useState("");
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [limit, setLimit] = useState(20);
  const [orderBy, setOrderBy] = useState<"time" | "relevance">("time");
  const [exportInfo, setExportInfo] = useState<string | null>(null);

  // 累积加载（游标分页）
  const [items, setItems] = useState<SynthesisRecord[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);

  const buildQuery = useCallback(
    (c?: string): HistoryQuery => ({
      voice_id: voiceId.trim() || undefined,
      emotion: emotion.trim() || undefined,
      text_search: textSearch.trim() || undefined,
      order_by: orderBy,
      only_failed: onlyFailed || undefined,
      limit,
      cursor: c,
    }),
    [voiceId, emotion, textSearch, orderBy, onlyFailed, limit],
  );

  /** 首次查询 / 换条件：重置列表。 */
  const reload = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [p, s, cl] = await Promise.all([
        historyApi.list(buildQuery()),
        historyApi.stats({
          voice_id: voiceId.trim() || undefined,
          emotion: emotion.trim() || undefined,
          text_search: textSearch.trim() || undefined,
          only_failed: onlyFailed || undefined,
        }),
        historyApi.cleanupHistory(5),
      ]);
      setPage(p);
      setStats(s);
      setCleanup(cl);
      setItems(p.items);
      setCursor(p.next_cursor);
      setHasMore(p.has_more);
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setLoading(false);
    }
  }, [buildQuery, voiceId, emotion, textSearch, onlyFailed]);

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

  /** 导出当前筛选结果。 */
  const doExport = useCallback(
    async (fmt: "csv" | "json") => {
      setExporting(true);
      setErr(null);
      setExportInfo("正在导出…");
      try {
        await historyApi.exportFile(buildQuery(), fmt, ({ total, limit: cap }) => {
          const n = Math.min(total, cap);
          setExportInfo(`正在导出 ${n} 条${total > cap ? `（共 ${total}，上限 ${cap}）` : ""}…`);
        });
        setExportInfo(null);
      } catch (e) {
        setErr(describeError(e));
        setExportInfo(null);
      } finally {
        setExporting(false);
      }
    },
    [buildQuery],
  );

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
            <label>文本搜索</label>
            <input
              value={textSearch}
              onChange={(e) => setTextSearch(e.target.value)}
              placeholder="全文搜索（≥3 字走索引，短词自动降级）"
            />
          </div>
          <div className="field">
            <label>排序</label>
            <select
              value={orderBy}
              onChange={(e) => setOrderBy(e.target.value as "time" | "relevance")}
            >
              <option value="time">按时间（最新在前）</option>
              <option value="relevance">按相关度（需全文搜索）</option>
            </select>
            <div className="hint" style={{ marginTop: 4 }}>
              相关度需搜索词 ≥3 字才生效，否则自动回退时间排序
            </div>
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
          <button
            className="ghost"
            onClick={() => void doExport("csv")}
            disabled={exporting}
          >
            {exporting ? "导出中…" : "导出 CSV"}
          </button>
          <button
            className="ghost"
            onClick={() => void doExport("json")}
            disabled={exporting}
          >
            导出 JSON
          </button>
        </div>
        <div className="hint" style={{ marginTop: 6 }}>
          导出会带上当前筛选条件（不含分页），CSV 带 UTF-8 BOM 可直接用 Excel 打开。
          {exportInfo && (
            <>
              <br />
              <span className="mono">{exportInfo}</span>
            </>
          )}
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

      {cleanup.length > 0 && (
        <Card title={`自动清理记录（最近 ${cleanup.length} 次）`}>
          <table>
            <thead>
              <tr>
                <th style={{ width: 100 }}>类型</th>
                <th style={{ width: 200 }}>清理界线</th>
                <th style={{ width: 90 }}>删除数量</th>
                <th style={{ width: 100 }}>触发方式</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {cleanup.map((c, i) => {
                const files = Array.isArray(c.detail?.files)
                  ? (c.detail.files as string[])
                  : [];
                return (
                  <tr key={`${c.created_at}-${i}`}>
                    <td>
                      {c.kind === "logs" ? (
                        <Badge kind="warn">日志文件</Badge>
                      ) : (
                        <Badge kind="dim">合成记录</Badge>
                      )}
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {c.cutoff}
                    </td>
                    <td className="mono">
                      {c.removed}
                      {c.kind === "logs" ? " 个文件" : " 条"}
                    </td>
                    <td>
                      {c.triggered_by === "scheduled" ? (
                        <Badge kind="dim">自动</Badge>
                      ) : (
                        <Badge kind="ok">手动</Badge>
                      )}
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {c.created_at}
                      {files.length > 0 && (
                        <div className="dim" style={{ fontSize: 10 }}>
                          删除：{files.join(", ")}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="hint" style={{ marginTop: 8 }}>
            清理由后台线程按保留期定期执行，也可通过{" "}
            <span className="mono">DELETE /api/v1/history?before=...</span> 手动触发。
            保留期在 <span className="mono">configs/gpu.yaml</span> 的{" "}
            <span className="mono">history_retain_days</span> 配置。
            <br />
            日志文件清理也会记入审计（含被删文件名），不再只记合成记录。
          </div>
        </Card>
      )}
    </>
  );
}
