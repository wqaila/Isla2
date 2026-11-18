/** 日志中心：真实日志查询（后端解析 JSON Lines）。 */

import { useCallback, useEffect, useState } from "react";
import { describeError, logsApi } from "../api/client";
import type { LogEntry, LogFile, LogPage } from "../api/types";
import {
  Alert,
  Badge,
  Card,
  ErrorBox,
  Metric,
  PageHead,
} from "../components/ui";

const LEVELS = ["INFO", "WARNING", "ERROR"] as const;

function levelKind(level: string): "ok" | "warn" | "err" | "dim" {
  switch (level) {
    case "ERROR":
    case "CRITICAL":
      return "err";
    case "WARNING":
      return "warn";
    case "INFO":
      return "ok";
    default:
      return "dim";
  }
}

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export default function LogsPage() {
  const [page, setPage] = useState<LogPage | null>(null);
  const [files, setFiles] = useState<LogFile[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // 查询条件
  const [limit, setLimit] = useState(100);
  const [levels, setLevels] = useState<string[]>([]);
  const [requestId, setRequestId] = useState("");
  const [keyword, setKeyword] = useState("");
  const [onlyErrors, setOnlyErrors] = useState(false);
  const [fileName, setFileName] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [p, f] = await Promise.all([
        logsApi.query({
          limit,
          levels,
          request_id: requestId.trim() || undefined,
          keyword: keyword.trim() || undefined,
          only_errors: onlyErrors,
          file_name: fileName || undefined,
        }),
        logsApi.files(),
      ]);
      setPage(p);
      setFiles(f);
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setLoading(false);
    }
  }, [limit, levels, requestId, keyword, onlyErrors, fileName]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleLevel = (lv: string) => {
    setLevels((prev) =>
      prev.includes(lv) ? prev.filter((x) => x !== lv) : [...prev, lv],
    );
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // 剪贴板不可用时静默（用户可手动选中）
    }
  };

  return (
    <>
      <PageHead
        title="日志中心"
        desc="服务端结构化日志（JSON Lines），由后端解析后返回，浏览器无需读文件。"
      />

      {err && <ErrorBox message={err} />}

      <Card title="查询条件">
        <div className="row">
          <div className="field">
            <label>条数上限</label>
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {[50, 100, 200, 500, 1000].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>级别</label>
            <div style={{ display: "flex", gap: 12, paddingTop: 6 }}>
              {LEVELS.map((lv) => (
                <label
                  key={lv}
                  style={{ display: "flex", alignItems: "center", gap: 4 }}
                >
                  <input
                    type="checkbox"
                    checked={levels.includes(lv)}
                    onChange={() => toggleLevel(lv)}
                  />
                  {lv}
                </label>
              ))}
            </div>
          </div>
          <div className="field">
            <label>request_id</label>
            <input
              value={requestId}
              onChange={(e) => setRequestId(e.target.value)}
              placeholder="精确匹配，如 2e1c4dbeec654d8c"
            />
          </div>
          <div className="field">
            <label>日志文件</label>
            <select value={fileName} onChange={(e) => setFileName(e.target.value)}>
              <option value="">当前日志（zjy14.log）</option>
              {files
                .filter((f) => !f.is_current)
                .map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.name}（{fmtSize(f.size)}）
                  </option>
                ))}
            </select>
            <div className="hint" style={{ marginTop: 4 }}>
              可查历史归档（按天轮转生成）
            </div>
          </div>
          <div className="field">
            <label>关键词</label>
            <input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="事件或字段值，如 合成完成"
            />
          </div>
        </div>

        <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 8 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={onlyErrors}
              onChange={(e) => setOnlyErrors(e.target.checked)}
            />
            只看错误（含异常堆栈）
          </label>
          <button onClick={() => void load()} disabled={loading}>
            {loading ? "查询中…" : "刷新"}
          </button>
        </div>
      </Card>

      {page && (
        <Card title={`查询结果（${page.count} 条）`}>
          <div className="grid grid-4" style={{ marginBottom: 12 }}>
            <Metric label="返回条数" value={page.count} />
            <Metric label="扫描行数" value={page.scanned} />
            <Metric label="坏行" value={page.bad_lines} />
            <Metric label="文件大小" value={fmtSize(page.file_size)} />
          </div>

          {page.bad_lines > 0 && (
            <Alert kind="warn">
              有 {page.bad_lines} 行无法解析（可能是进程被中断导致的截断行）。
              这些行已跳过，不影响其它日志。
            </Alert>
          )}

          {page.has_more && (
            <div className="hint" style={{ marginBottom: 8 }}>
              还有更早的日志未显示。可提高「条数上限」或用条件缩小范围。
            </div>
          )}

          <div style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }}>
            {page.entries.length === 0 ? (
              <div className="empty">没有匹配的日志</div>
            ) : (
              page.entries.map((e) => <LogRow key={`${e.line_no}-${e.ts}`} e={e} onCopy={copy} />)
            )}
          </div>
        </Card>
      )}

      <Card title={`日志文件（${files.length}）`}>
        {files.length === 0 ? (
          <div className="empty">日志目录为空</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>文件名</th>
                <th style={{ width: 100 }}>大小</th>
                <th style={{ width: 180 }}>修改时间</th>
                <th style={{ width: 80 }}>状态</th>
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.name}>
                  <td className="mono" style={{ fontSize: 11 }}>
                    {f.name}
                  </td>
                  <td className="mono">{fmtSize(f.size)}</td>
                  <td className="mono" style={{ fontSize: 11 }}>
                    {f.modified_at}
                  </td>
                  <td>
                    {f.is_current ? (
                      <Badge kind="ok">当前</Badge>
                    ) : (
                      <Badge kind="dim">归档</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Alert kind="info">
        日志路径：<span className="mono">{page?.path ?? "（未知）"}</span>
        <br />
        格式：每行一个 JSON，字段含 ts / level / logger / event / request_id / 业务字段。
        敏感字段（api_key / auth_token）在写入与读取时各脱敏一次。
      </Alert>
    </>
  );
}

function LogRow({ e, onCopy }: { e: LogEntry; onCopy: (t: string) => void }) {
  const [open, setOpen] = useState(false);
  const fields = e.fields ?? {};
  const hasDetail = Object.keys(fields).length > 0 || Boolean(e.exc);

  return (
    <div
      style={{
        borderBottom: "1px solid var(--border)",
        padding: "6px 0",
      }}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <span className="dim" style={{ minWidth: 44 }}>
          #{e.line_no}
        </span>
        <Badge kind={levelKind(e.level)}>{e.level}</Badge>
        <span className="dim" style={{ fontSize: 11 }}>
          {e.ts}
        </span>
        <span style={{ flex: 1 }}>{e.event}</span>
        {hasDetail && (
          <button className="ghost sm" onClick={() => setOpen((v) => !v)}>
            {open ? "收起" : "详情"}
          </button>
        )}
        {e.request_id && (
          <button className="ghost sm" onClick={() => onCopy(e.request_id!)}>
            复制 ID
          </button>
        )}
      </div>

      {e.request_id && (
        <div className="dim" style={{ fontSize: 11, marginLeft: 52 }}>
          request_id: <span className="mono">{e.request_id}</span>
        </div>
      )}

      {open && (
        <div style={{ marginLeft: 52, marginTop: 6 }}>
          <div className="dim" style={{ fontSize: 11, marginBottom: 4 }}>
            logger: <span className="mono">{e.logger}</span>
          </div>
          {Object.keys(fields).length > 0 && (
            <pre style={{ margin: 0, fontSize: 11 }}>
              {JSON.stringify(fields, null, 2)}
            </pre>
          )}
          {e.exc && (
            <pre
              style={{
                margin: "6px 0 0",
                fontSize: 11,
                color: "var(--err, #f88)",
                whiteSpace: "pre-wrap",
              }}
            >
              {e.exc}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
