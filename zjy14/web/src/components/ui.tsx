/** 共用 UI 组件。 */

import type { ReactNode } from "react";

export function PageHead({ title, desc }: { title: string; desc?: string }) {
  return (
    <div className="page-head">
      <h2>{title}</h2>
      {desc && <p>{desc}</p>}
    </div>
  );
}

export function Card({
  title,
  children,
  actions,
}: {
  /** 卡片标题。接受 ReactNode 以便带状态徽章（设备监控页需要）。 */
  title?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="card">
      {(title || actions) && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 12,
          }}
        >
          {title && <h3 style={{ margin: 0 }}>{title}</h3>}
          {actions}
        </div>
      )}
      {children}
    </div>
  );
}

export function Metric({
  label,
  value,
  unit,
  hint,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  /**
   * 指标下方的补充说明（灰色小字）。
   * 用途：标注「偏慢」「已超阈值」「等待中」这类状态判断，
   * 避免把判断塞进 value 里导致数字与说明混在一起。
   */
  hint?: ReactNode;
}) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">
        {value}
        {unit && <span className="metric-unit">{unit}</span>}
      </div>
      {hint && (
        <div
          className="dim"
          style={{ fontSize: 10, marginTop: 2, lineHeight: 1.3 }}
        >
          {hint}
        </div>
      )}
    </div>
  );
}

export function Alert({
  kind,
  children,
}: {
  kind: "info" | "warn" | "err";
  children: ReactNode;
}) {
  const cls = kind === "warn" ? "alert-warn" : kind === "err" ? "alert-err" : "alert-info";
  return <div className={`alert ${cls}`}>{children}</div>;
}

export function Badge({
  kind,
  children,
}: {
  kind: "ok" | "warn" | "err" | "dim";
  children: ReactNode;
}) {
  return <span className={`badge badge-${kind}`}>{children}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading({ text = "加载中" }: { text?: string }) {
  return (
    <div className="empty">
      <span className="spinner" />
      {text}…
    </div>
  );
}

/** 错误提示。不静默吞错，总是展示。 */
export function ErrorBox({ message }: { message: string }) {
  return <Alert kind="err">{message}</Alert>;
}
