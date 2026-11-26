/** 语音链路启动：一键拉起 ASR / LLM / TTS / 网关。
 *
 * 为什么这一页独立于「系统设置」：
 *   系统设置展示的是**当前 zjy14 进程**的配置（只读）；
 *   本页操作的是**操作系统里的其他进程**，而且必须在 zjy14 没起来时
 *   也能用 —— 所以它对接的是独立启动器（:50100），不是 zjy14 自身。
 *
 * TTS 说明（需求重点）：
 *   本页启动的 TTS 是 zjy14 自己的 CosyVoice3（WSL，:50001），
 *   并在启动网关时注入 TTS_BASE_URL=:50001，
 *   因此板子走的是 zjy14 引擎，不是 zjy13 自带的 tts_server_wsl（:50000）。
 */

import { useCallback, useEffect, useState } from "react";
import {
  launcherApi,
  type LauncherActionResult,
  type LauncherService,
} from "../api/launcher";
import { Alert, Badge, Card, ErrorBox, Loading, PageHead } from "../components/ui";

type Busy = string | null;

function stateBadge(s: LauncherService) {
  if (s.healthy) return <Badge kind="ok">健康</Badge>;
  if (s.running) return <Badge kind="warn">运行中·未就绪</Badge>;
  if (s.kind === "probe") return <Badge kind="err">配置异常</Badge>;
  return <Badge kind="dim">未运行</Badge>;
}

function kindLabel(k: string) {
  if (k === "wsl") return "WSL";
  if (k === "probe") return "探测";
  return "Windows";
}

export default function ServicesPage() {
  const [online, setOnline] = useState<boolean | null>(null);
  const [services, setServices] = useState<LauncherService[]>([]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [toast, setToast] = useState<{ text: string; kind: "ok" | "err" } | null>(null);
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [engine, setEngine] = useState("cosyvoice3");

  const refresh = useCallback(async () => {
    const res = await launcherApi.status();
    if (!res.online) {
      setOnline(false);
      setError(res.error ?? "启动器不可达");
      return;
    }
    if (res.error || !res.data) {
      setOnline(true);
      setError(res.error ?? "读取状态失败");
      return;
    }
    setOnline(true);
    setError(null);
    setServices(res.data.services);
    setReady(res.data.ready);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(() => {
      if (!busy) refresh();
    }, 4000);
    return () => clearInterval(t);
  }, [refresh, busy]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  const withBusy = async (key: string, fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(key);
    try {
      await fn();
    } finally {
      setBusy(null);
      await refresh();
    }
  };

  const onStartAll = () =>
    withBusy("start-all", async () => {
      setToast({ text: "正在按依赖顺序启动，TTS 首次加载模型可能需要 1~3 分钟…", kind: "ok" });
      const res = await launcherApi.startAll(engine);
      if (!res.online) {
        setToast({ text: "启动器未运行", kind: "err" });
        return;
      }
      const r = res.data?.result;
      if (r?.ok) {
        setToast({ text: "全链路启动完成 ✓", kind: "ok" });
      } else {
        const bad = (r?.results ?? []).filter((x) => !x.ok);
        setToast({
          text: "未全部成功：" + bad.map((x) => `${x.name}: ${x.message ?? ""}`).join("；"),
          kind: "err",
        });
      }
    });

  const onStopAll = () =>
    withBusy("stop-all", async () => {
      const res = await launcherApi.stopAll();
      const r = res.data?.result;
      setToast(
        r?.ok
          ? { text: "已停止全部服务", kind: "ok" }
          : { text: "部分服务端口仍被占用（见卡片详情）", kind: "err" },
      );
    });

  const onStart = (name: string) =>
    withBusy(`start:${name}`, async () => {
      const res = await launcherApi.start(name, engine);
      const d: LauncherActionResult | undefined = res.data;
      setToast({
        text: d?.message ?? res.error ?? (res.online ? "已发起启动" : "启动器未运行"),
        kind: d?.ok ? "ok" : "err",
      });
    });

  const onStop = (name: string) =>
    withBusy(`stop:${name}`, async () => {
      const res = await launcherApi.stop(name);
      const d = res.data;
      setToast({
        text: d?.message ?? res.error ?? "已停止",
        kind: d?.ok ? "ok" : "err",
      });
    });

  const onLlmTest = () =>
    withBusy("llm-test", async () => {
      const res = await launcherApi.llmTest();
      const d = res.data;
      setToast(
        d?.ok
          ? { text: `LLM 连通 ✓ 路线=${d.route ?? "?"}（${d.ms ?? "?"}ms）`, kind: "ok" }
          : { text: `LLM 失败：${d?.error ?? res.error ?? "未知"}`, kind: "err" },
      );
    });

  const onLog = async (name: string) => {
    const res = await launcherApi.log(name, 80);
    setLogs((prev) => ({
      ...prev,
      [name]: res.data?.log ?? res.error ?? "读取失败",
    }));
  };

  // ---- 启动器不可达：这才是「一键启动」的主场景 ----
  if (online === false) {
    return (
      <>
        <PageHead
          title="语音链路启动"
          desc="一键拉起 ASR / LLM / TTS / 网关"
        />
        <Alert kind="warn">
          启动器未运行（:50100）。请在 <span className="mono">zjy14</span> 目录执行：
          <div className="mono" style={{ marginTop: 8 }}>
            .\scripts\start_launcher.ps1
          </div>
          <div style={{ marginTop: 8 }}>
            然后打开 <span className="mono">http://127.0.0.1:50100/</span>{" "}
            使用完整控制台（自带页面，不依赖本界面）。
          </div>
        </Alert>
        <Card title="为什么是另一个进程">
          <div className="hint">
            TTS 首次加载模型要十几秒，而「一键启动」必须在 TTS
            <b>还没起来</b>时就能点 —— 所以控制台不能依赖 zjy14 自身。
            启动器只用 Python 标准库，跑在 Windows 侧（只有它能同时拉起
            Windows 的 ASR 和 WSL 的 TTS）。
          </div>
        </Card>
        {error && <ErrorBox message={error} />}
      </>
    );
  }

  return (
    <>
      <PageHead
        title="语音链路启动"
        desc="一键拉起 ASR / LLM / TTS / 网关。TTS 使用 zjy14 的 CosyVoice3（:50001）。"
      />

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
        <button className="primary" onClick={onStartAll} disabled={!!busy}>
          {busy === "start-all" ? "启动中…" : "一键启动全部"}
        </button>
        <button className="ghost" onClick={onStopAll} disabled={!!busy}>
          {busy === "stop-all" ? "停止中…" : "全部停止"}
        </button>
        <button className="ghost sm" onClick={refresh} disabled={!!busy}>
          刷新状态
        </button>
        <select
          value={engine}
          onChange={(e) => setEngine(e.target.value)}
          style={{ marginLeft: 4 }}
        >
          <option value="cosyvoice3">TTS 引擎：cosyvoice3（真模型）</option>
          <option value="stub">TTS 引擎：stub（只验链路）</option>
        </select>
        <span style={{ marginLeft: "auto" }}>
          {ready ? <Badge kind="ok">全链路就绪</Badge> : <Badge kind="warn">未就绪</Badge>}
        </span>
      </div>

      {toast && (
        <Alert kind={toast.kind === "ok" ? "info" : "warn"}>{toast.text}</Alert>
      )}

      {online === null ? (
        <Loading />
      ) : (
        <div className="grid grid-2">
          {services.map((s) => (
            <Card key={s.name} title={`${s.label}（${kindLabel(s.kind)}）`}>
              <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 8 }}>
                {stateBadge(s)}
                {s.port ? <span className="mono dim">:{s.port}</span> : null}
                {s.pid ? <span className="hint">PID {s.pid}</span> : null}
              </div>

              <div className="hint" style={{ marginBottom: 10 }}>
                {s.detail}
              </div>

              {s.depends_on.length > 0 && (
                <div className="hint" style={{ marginBottom: 10 }}>
                  依赖：<span className="mono">{s.depends_on.join(", ")}</span>
                </div>
              )}

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  className="sm"
                  onClick={() => onStart(s.name)}
                  disabled={!!busy || s.healthy}
                >
                  启动
                </button>
                <button
                  className="sm"
                  onClick={() => onStop(s.name)}
                  disabled={!!busy || !s.running}
                >
                  停止
                </button>
                {s.kind === "probe" && (
                  <button className="sm" onClick={onLlmTest} disabled={!!busy}>
                    测试连通性
                  </button>
                )}
                <button className="ghost sm" onClick={() => onLog(s.name)}>
                  日志
                </button>
              </div>

              {logs[s.name] !== undefined && (
                <pre
                  className="mono"
                  style={{
                    marginTop: 10,
                    maxHeight: 220,
                    overflow: "auto",
                    fontSize: 11,
                    background: "rgba(0,0,0,.25)",
                    padding: 10,
                    borderRadius: 6,
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {logs[s.name]}
                </pre>
              )}
            </Card>
          ))}
        </div>
      )}

      <Alert kind="info">
        <b>TTS 归属：</b>本页启动 <span className="mono">zjy14</span> 的
        CosyVoice3（WSL，<span className="mono">:50001</span>），启动网关时注入
        <span className="mono"> TTS_BASE_URL=http://127.0.0.1:50001</span>，
        因此板子听到的是 zjy14 的引擎，而不是 zjy13 自带的
        <span className="mono"> tts_server_wsl</span>（:50000）。
        <br />
        <b>ASR 归属：</b>独立服务 <span className="mono">zjy13/server/asr_service.py</span>
        （<span className="mono">:50002</span>），网关用
        <span className="mono"> --asr-url</span> 调用它，本机不再重复加载 FunASR。
      </Alert>

      {error && <ErrorBox message={error} />}
    </>
  );
}