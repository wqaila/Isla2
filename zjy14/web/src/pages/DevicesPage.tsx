/** 设备管理：ESP32-S3 实时状态监控。
 *
 * 数据来自 zjy13 的只读端点 /api/devices（与 WebSocket 同端口 8765）。
 * zjy14 本身不知道设备存在 —— 它只是 TTS 引擎。
 *
 * 本页只做监控，不做控制：
 *   - 不提供重启/OTA/改配置
 *   - 不伪造在线数据（无设备就是无设备）
 *   - 字段缺失显示「—」，不猜值
 */

import { useState } from "react";
import { deviceApi } from "../api/client";
import type { DeviceClient, DeviceTimings } from "../api/types";
import { Alert, Badge, Card, Metric, PageHead } from "../components/ui";
import { useDeviceMonitor } from "../hooks/useDeviceMonitor";

/** 8 种状态的中文名与徽章样式（与 zjy13 states.py 一致） */
const STATE_META: Record<
  string,
  { label: string; kind: "ok" | "warn" | "err" | "dim"; color: string }
> = {
  idle: { label: "空闲", kind: "dim", color: "#888" },
  recording: { label: "录音中", kind: "ok", color: "#4a9eff" },
  asr_processing: { label: "识别中", kind: "ok", color: "#00c8c8" },
  llm_processing: { label: "生成中", kind: "ok", color: "#a06eff" },
  tts_processing: { label: "合成中", kind: "ok", color: "#ff9a3c" },
  filler: { label: "填充音", kind: "warn", color: "#e8c547" },
  playing: { label: "播放中", kind: "ok", color: "#3fbf5f" },
  error: { label: "错误", kind: "err", color: "#e04b4b" },
};

/** 心跳阈值（与 zjy13 server.py 的 HEARTBEAT_TIMEOUT_S 一致） */
const HEARTBEAT_TIMEOUT_S = 30;
/** 单轮超时（与 TURN_TIMEOUT_S 一致） */
const TURN_TIMEOUT_S = 60;

function stateMeta(s: string) {
  return (
    STATE_META[s] ?? { label: s || "未知", kind: "dim" as const, color: "#888" }
  );
}

function fmtSec(s: number): string {
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rest = Math.round(s % 60);
  if (m < 60) return `${m}m${rest}s`;
  return `${Math.floor(m / 60)}h${m % 60}m`;
}

function fmtMs(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return `${Math.round(v)}ms`;
}

/** 耗时是否异常（超过阈值的标红） */
function slow(v: number | null, warnMs: number): boolean {
  return v !== null && v !== undefined && v > warnMs;
}

export default function DevicesPage() {
  const [intervalMs, setIntervalMs] = useState(1000);
  const { snapshot, lastOkAt, failCount, error, paused, refresh } =
    useDeviceMonitor(intervalMs);

  const server = snapshot?.server;
  const clients = snapshot?.clients ?? [];
  const counters = snapshot?.counters;

  const full = server ? counters?.active !== undefined &&
    counters.active >= server.max_connections : false;

  return (
    <>
      <PageHead
        title="设备管理"
        desc="ESP32-S3 实时状态。数据来自 zjy13 的只读端点，每 1 秒刷新。"
      />

      {/* ---- 顶部状态条 ---- */}
      <Card>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 16,
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span
              style={{
                width: 9,
                height: 9,
                borderRadius: "50%",
                background: error ? "#e04b4b" : paused ? "#888" : "#3fbf5f",
                display: "inline-block",
              }}
            />
            <strong>
              {error ? "监控服务不可达" : paused ? "已暂停（页面不可见）" : "服务运行中"}
            </strong>
          </div>

          <span className="mono dim" style={{ fontSize: 11 }}>
            {deviceApi.baseUrl}
          </span>

          {server && (
            <>
              <span className="dim">
                角色 <strong>{server.character_name || server.character_id}</strong>
              </span>
              <span className="dim">
                连接{" "}
                <strong style={{ color: full ? "#e8c547" : undefined }}>
                  {counters?.active ?? 0}/{server.max_connections}
                </strong>
                {full && <Badge kind="warn">已满</Badge>}
              </span>
              <span className="dim">运行 {fmtSec(server.uptime_s)}</span>
              <span className="dim">
                累计 {counters?.total_turns ?? 0} 轮
              </span>
              <span className="dim">
                错误{" "}
                <strong
                  style={{
                    color: (counters?.total_errors ?? 0) > 0 ? "#e04b4b" : undefined,
                  }}
                >
                  {counters?.total_errors ?? 0}
                </strong>
              </span>
            </>
          )}

          <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
            <select
              value={intervalMs}
              onChange={(e) => setIntervalMs(Number(e.target.value))}
              style={{ fontSize: 12 }}
            >
              {[500, 1000, 2000, 5000].map((n) => (
                <option key={n} value={n}>
                  {n}ms
                </option>
              ))}
            </select>
            <button className="ghost sm" onClick={refresh}>
              刷新
            </button>
          </div>
        </div>

        {/* 数据新鲜度 */}
        <div className="hint" style={{ marginTop: 8 }}>
          {error ? (
            <>
              <span style={{ color: "#e04b4b" }}>
                无法连接监控服务（连续失败 {failCount} 次）：{error}
              </span>
              <br />
              排查：① zjy13 的 WebSocket 服务是否启动 ② 地址是否为{" "}
              <span className="mono">{deviceApi.baseUrl}</span>{" "}
              ③ 浏览器是否能访问该端口
            </>
          ) : lastOkAt ? (
            <>
              最近更新 {lastOkAt}
              {failCount > 0 && (
                <span style={{ color: "#e8c547" }}>
                  （有 {failCount} 次失败，数据可能过期）
                </span>
              )}
            </>
          ) : (
            "正在获取…"
          )}
        </div>
      </Card>

      {/* ---- 无设备 ---- */}
      {clients.length === 0 && !error && (
        <Card title="设备列表">
          <div className="empty">
            暂无设备连接
            <div
              className="hint"
              style={{ marginTop: 12, textAlign: "left", maxWidth: 560, margin: "12px auto 0" }}
            >
              排查顺序：
              <br />
              1. 板子是否连上 WiFi
              <br />
              2. 板子里的服务地址是否为{" "}
              <span className="mono">
                ws://&lt;主机IP&gt;:{server?.port ?? 8765}
              </span>
              <br />
              3. 是否超过最大连接数（当前上限 {server?.max_connections ?? 4}）
              {server?.access_token_required && (
                <>
                  <br />
                  4. 服务端已启用 access_token，板子是否带了正确令牌
                </>
              )}
              <br />
              5. 查看 zjy13 日志：
              <span className="mono"> grep "连接" &lt;logfile&gt;</span>
            </div>
          </div>
        </Card>
      )}

      {/* ---- 设备卡片 ---- */}
      {clients.map((c) => (
        <DeviceCard key={c.session_id} c={c} />
      ))}

      {/* ---- 协议约定（折叠说明） ---- */}
      <Card title="协议与阈值说明">
        <div className="hint">
          状态机 8 种：idle / recording / asr_processing / llm_processing /{" "}
          tts_processing / filler / playing / error。
          <br />
          心跳超时阈值 <span className="mono">{HEARTBEAT_TIMEOUT_S}s</span>
          （来自 zjy13 <span className="mono">HEARTBEAT_TIMEOUT_S</span>），
          单轮超时 <span className="mono">{TURN_TIMEOUT_S}s</span>。
          <br />
          音频格式：上行 {server?.audio_in_sample_rate ?? 16000}Hz /
          {server?.audio_in_channels ?? 4} 通道，下行{" "}
          {server?.audio_out_sample_rate ?? 24000}Hz 单声道 PCM S16LE。
          <br />
          协议版本 v{server?.protocol_version ?? 1}。
          填充音{server?.filler_available ? "可用" : "不可用"}。
          {server?.mean_latency_s !== null && server?.mean_latency_s !== undefined && (
            <>
              {" "}
              历史平均延迟{" "}
              <span className="mono">{server.mean_latency_s.toFixed(2)}s</span>。
            </>
          )}
        </div>
        <div className="hint" style={{ marginTop: 8 }}>
          本页为只读监控：不提供重启 / OTA / 改配置，也不伪造在线数据。
        </div>
      </Card>
    </>
  );
}

// ----------------------------------------------------------------------
// 单块设备卡片
// ----------------------------------------------------------------------

function DeviceCard({ c }: { c: DeviceClient }) {
  // 快照生成失败的占位
  if (c.error) {
    return (
      <Card title={c.session_id}>
        <Alert kind="warn">
          该设备的快照生成失败：{c.error}
          <br />
          其它设备不受影响。
        </Alert>
      </Card>
    );
  }

  const meta = stateMeta(String(c.state));
  const hb = c.last_heartbeat_s;
  const hbBad = !c.heartbeat_ok;
  const hbWarn = hb >= 10 && !hbBad;

  // 状态卡住判定：非 idle 且停留超过单轮超时
  const stuck =
    c.state !== "idle" && c.state_elapsed_ms > TURN_TIMEOUT_S * 1000;

  const audio = c.audio;
  const pct =
    audio.progress !== null
      ? Math.round(audio.progress * 100)
      : null;

  return (
    <Card
      title={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <span>{c.device_id || c.session_id.slice(0, 8)}</span>
          <Badge kind={meta.kind}>{meta.label}</Badge>
          {hbBad && <Badge kind="err">心跳超时</Badge>}
          {stuck && <Badge kind="warn">可能卡住</Badge>}
        </span>
      }
    >
      {/* 身份与健康 */}
      <div className="grid grid-4" style={{ marginBottom: 12 }}>
        <Metric label="会话" value={c.session_id.slice(0, 8)} />
        <Metric label="对端" value={c.peer || "—"} />
        <Metric label="已连接" value={fmtSec(c.connected_s)} />
        <Metric
          label="心跳"
          value={`${hb.toFixed(1)}s 前`}
          hint={hbBad ? "已超过 30s 阈值" : hbWarn ? "偏久" : "正常"}
        />
      </div>

      {/* 本轮进度 */}
      <div style={{ marginBottom: 12 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: 12,
            marginBottom: 4,
          }}
        >
          <span>
            本轮音频下发
            {audio.streaming ? (
              <Badge kind="dim">流式</Badge>
            ) : null}
          </span>
          <span className="mono">
            {audio.sent_frames}
            {audio.expect_frames > 0 ? ` / ${audio.expect_frames}` : ""} 帧
            {pct !== null ? `（${pct}%）` : ""}
          </span>
        </div>
        <div
          style={{
            height: 6,
            background: "var(--border, #333)",
            borderRadius: 3,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: pct !== null ? `${pct}%` : "100%",
              background: meta.color,
              opacity: pct !== null ? 1 : 0.4,
              transition: "width 0.3s ease",
            }}
          />
        </div>
        <div className="hint" style={{ marginTop: 4 }}>
          {audio.streaming
            ? "流式模式：总长未知，按已发帧数显示"
            : `已发 ${audio.sent_bytes} / ${audio.expect_bytes} 字节`}
          {c.filler_session_open && (
            <>
              {" · "}
              <Badge kind="warn">填充音播放中</Badge>
            </>
          )}
        </div>
      </div>

      {/* 阶段耗时 */}
      <Timings t={c.timings} />

      {/* 文本 */}
      {(c.last_asr_text || c.last_reply_text) && (
        <div className="hint" style={{ marginTop: 10 }}>
          {c.last_asr_text && (
            <>
              识别：
              <span className="mono">{c.last_asr_text}</span>
              <br />
            </>
          )}
          {c.last_reply_text && (
            <>
              回复：
              <span className="mono">
                {c.last_reply_text.length > 60
                  ? `${c.last_reply_text.slice(0, 60)}…`
                  : c.last_reply_text}
              </span>
            </>
          )}
        </div>
      )}

      {/* 错误 */}
      <div style={{ marginTop: 10 }}>
        <div className="dim" style={{ fontSize: 11, marginBottom: 4 }}>
          最近错误
        </div>
        {c.errors_recent.length === 0 ? (
          <div className="hint">无</div>
        ) : (
          <div style={{ fontSize: 11 }}>
            {c.errors_recent
              .slice()
              .reverse()
              .map((e, i) => (
                <div key={`${e.at}-${i}`} style={{ marginBottom: 2 }}>
                  <Badge kind="err">{e.code}</Badge>{" "}
                  <span className="dim">{e.at}</span>{" "}
                  <span>{e.message}</span>
                </div>
              ))}
          </div>
        )}
      </div>

      {/* 其它细节 */}
      <div className="hint" style={{ marginTop: 10, fontSize: 11 }}>
        turn_id <span className="mono">{c.turn_id || "—"}</span>
        {" · "}seq {c.turn_seq}
        {" · "}对话 {c.turns_done} 轮
        {" · "}握手 {c.hello_done ? "完成" : "未完成"}
        {" · "}
        {c.is_busy ? "忙" : "闲"}
      </div>
    </Card>
  );
}

function Timings({ t }: { t: DeviceTimings }) {
  return (
    <div className="grid grid-4">
      <Metric
        label="ASR"
        value={fmtMs(t.asr_ms)}
        hint={slow(t.asr_ms, 1000) ? "偏慢" : undefined}
      />
      <Metric
        label="LLM 首字"
        value={fmtMs(t.llm_first_ms)}
        hint={slow(t.llm_first_ms, 1500) ? "偏慢" : undefined}
      />
      <Metric
        label="TTS 首块"
        value={fmtMs(t.tts_first_ms)}
        hint={slow(t.tts_first_ms, 3000) ? "偏慢" : undefined}
      />
      <Metric
        label="播放"
        value={fmtMs(t.playback_ms)}
        hint={t.playback_ms === null ? "等 playback_done" : undefined}
      />
    </div>
  );
}
