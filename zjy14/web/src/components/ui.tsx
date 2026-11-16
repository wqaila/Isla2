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
  title?: string;
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
}: {
  label: string;
  value: ReactNode;
  unit?: string;
}) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">
        {value}
        {unit && <span className="metric-unit">{unit}</span>}
      </div>
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
