/** 日志中心：服务端结构化日志的说明与本地调试指引。
 *
 * 后端日志是 JSON Lines 落在 WSL 本地 ~/zjy14-data/logs/zjy14.log。
 * 浏览器无法直接读该文件，所以本页提供：
 *   1. 日志格式说明
 *   2. 常用排查命令（可复制）
 *   3. 当前会话的合成记录（来自 /api/v1/system/status 与本地历史）
 */

import { useCallback, useState } from "react";
import { systemApi } from "../api/client";
import { Alert, Card, ErrorBox, Loading, PageHead } from "../components/ui";
import { useAsync } from "../hooks/useAsync";

const COMMANDS = [
  {
    label: "查看最近 50 行",
    cmd: "tail -n 50 ~/zjy14-data/logs/zjy14.log",
  },
  {
    label: "只看警告与错误",
    cmd: "grep -E '\"level\": \"(WARNING|ERROR)\"' ~/zjy14-data/logs/zjy14.log | tail -n 30",
  },
  {
    label: "按 request_id 追踪",
    cmd: "grep '<request_id>' ~/zjy14-data/logs/zjy14.log",
  },
  {
    label: "统计合成耗时",
    cmd: "grep '合成完成' ~/zjy14-data/logs/zjy14.log | tail -n 20",
  },
  {
    label: "查看音色注册情况",
    cmd: "grep -E '音色已注册|参考文本校验' ~/zjy14-data/logs/zjy14.log",
  },
  {
    label: "查看文本前端是否启用",
    cmd: "grep '文本前端' ~/zjy14-data/logs/zjy14.log",
  },
];

export default function LogsPage() {
  const status = useAsync(useCallback(() => systemApi.status(), []));
  const [copied, setCopied] = useState<string | null>(null);

  const copy = async (cmd: string) => {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(cmd);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      // 剪贴板不可用时降级为选中提示
      alert("浏览器不允许访问剪贴板，请手动复制");
    }
  };

  const logPath = status.data?.paths?.["log_dir"] ?? "~/zjy14-data/logs";

  return (
    <>
      <PageHead
        title="日志中心"
        desc="结构化日志（JSON Lines）落在 WSL 本地，浏览器无法直读，此处提供排查命令。"
      />

      <Alert kind="info">
        日志路径：<span className="mono">{logPath}/zjy14.log</span>
        <br />
        格式：每行一个 JSON，字段含 ts / level / event / request_id / 业务字段。
        敏感字段（api_key / auth_token）自动脱敏。
      </Alert>

      {status.error && <ErrorBox message={status.error} />}
      {status.loading && <Loading />}

      <Card title="常用排查命令（点击复制）">
        <table>
          <thead>
            <tr>
              <th style={{ width: 200 }}>用途</th>
              <th>命令</th>
              <th style={{ width: 90 }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {COMMANDS.map((c) => (
              <tr key={c.label}>
                <td>{c.label}</td>
                <td className="mono" style={{ fontSize: 11, wordBreak: "break-all" }}>
                  {c.cmd}
                </td>
                <td>
                  <button className="ghost sm" onClick={() => copy(c.cmd)}>
                    {copied === c.cmd ? "已复制" : "复制"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="日志字段说明">
        <table>
          <thead>
            <tr>
              <th style={{ width: 160 }}>字段</th>
              <th>含义</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="mono">ts</td>
              <td className="dim">ISO8601 时间戳（本地时区）</td>
            </tr>
            <tr>
              <td className="mono">level</td>
              <td className="dim">INFO / WARNING / ERROR</td>
            </tr>
            <tr>
              <td className="mono">event</td>
              <td className="dim">事件描述（中文）</td>
            </tr>
            <tr>
              <td className="mono">request_id</td>
              <td className="dim">
                请求追踪 ID，由中间件注入，可用响应头 x-request-id 对账
              </td>
            </tr>
            <tr>
              <td className="mono">voice_id / chars / duration_s</td>
              <td className="dim">合成业务字段</td>
            </tr>
            <tr>
              <td className="mono">rtf / total_ms</td>
              <td className="dim">性能指标</td>
            </tr>
            <tr>
              <td className="mono">segments</td>
              <td className="dim">分句数量（停顿控制生效时会 &gt; 1）</td>
            </tr>
            <tr>
              <td className="mono">emotion_meta</td>
              <td className="dim">
                情绪标签（仅记录，不参与合成 —— 见 D1 结论）
              </td>
            </tr>
          </tbody>
        </table>
      </Card>

      <Card title="数据库中的合成记录">
        <div className="hint">
          每次合成都会写入 SQLite 的 <span className="mono">synthesis_logs</span> 表，
          包含 request_id、文本、参数、时长、RTF、错误码。可用以下命令查询：
        </div>
        <pre style={{ marginTop: 10 }}>
{`sqlite3 ~/zjy14-data/custom_voice.db \\
  "SELECT created_at, voice_id, duration_s, rtf, emotion
   FROM synthesis_logs ORDER BY id DESC LIMIT 20;"`}
        </pre>
      </Card>

      <Alert kind="warn">
        日志里不会记录 API Key、令牌或完整音频内容。
        角色声音仅限用户拥有或明确获授权的素材。
      </Alert>
    </>
  );
}
