/** 语音链路启动：一键拉起 ASR / LLM / TTS / 网关。
 *
 * ## 与独立控制台（:50100）的关系
 *
 * 本页是**整合版**：把 `launcher/static/index.html` 那个自包含控制台的能力
 * 全部搬进主界面（同样对接启动器 API），并补上了主界面才有的视觉与信息层级。
 *
 * - 独立控制台仍然保留，作为**兜底**：它不需要 node、不需要 zjy14 后端，
 *   只有 Python 标准库就能起。
 * - 本页需要 Vite（node）在跑，但**不需要 zjy14 后端** ——
 *   所以它同样能做冷启动（见 scripts/start_web.ps1）。
 *
 * ## 为什么这一页能「冷启动」
 *
 * 启动器是独立进程（:50100），而本页是静态前端（Vite dev）。
 * 两者都不依赖 zjy14 的 :50001，所以 TTS 还没起来时本页照样能打开并操作。
 *
 * ## TTS 归属（需求重点）
 *
 * 这里启动的 TTS 是 **zjy14 自己的 CosyVoice3**（WSL，:50001），
 * 并在启动网关时注入 `TTS_BASE_URL=:50001`，
 * 因此板子走 zjy14 引擎，不是 zjy13 自带的 tts_server_wsl（:50000）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  launcherApi,
  type LauncherActionResult,
  type LauncherService,
} from "../api/launcher";
import { Alert, Badge, Card, Empty, ErrorBox, Loading, PageHead } from "../components/ui";

/** 进度状态：一键启动/停止期间展示，避免「点了没反应」的错觉。 */
interface Progress {
  title: string;
  steps: string[];
  pct: number;
  startedAt: number;
}

function kindLabel(k: string): string {
  if (k === "wsl") return "WSL";
  if (k === "probe") return "探测";
  return "Windows";
}

function stateBadge(s: LauncherService) {
  if (s.healthy) return <Badge kind="ok">健康</Badge>;
  if (s.running) return <Badge kind="warn">运行中·未就绪</Badge>;
  if (s.kind === "probe") return <Badge kind="err">配置异常</Badge>;
  return <Badge kind="dim">未运行</Badge>;
}

/** 把 health 响应里的关键字段渲染成键值对（只挑有意义的，避免刷屏）。 */
function HealthDetail({ service }: { service: LauncherService }) {
  const h = service.health;
  if (!h || typeof h !== "object") return null;
  const o = h as Record<string, unknown>;
  const rows: Array<[string, string]> = [];

  const push = (k: string, v: unknown) => {
    if (v === undefined || v === null || v === "") return;
    rows.push([k, typeof v === "string" ? v : JSON.stringify(v)]);
  };

  if (service.kind === "probe") {
    push("模型", o.model);
    push("地址", o.base_url);
    push("代理", o.proxy ?? "直连");
    push("密钥", o.api_key_set ? "已配置" : "缺失");
    const warns = o.warnings;
    if (Array.isArray(warns) && warns.length) {
      push("注意", warns.join("；"));
    }
  } else if (service.name === "tts") {
    push("引擎", o.engine);
    push("设备", o.device);
    push("采样率", o.sample_rate ? `${o.sample_rate} Hz` : undefined);
    const voices = o.voices;
    if (Array.isArray(voices)) {
      push("音色", voices.length ? voices.map((v) => String(v)).join(", ") : "无");
    }
  } else if (service.name === "asr") {
    push("设备", o.device);
    push("模型", o.model);
    push("加载", typeof o.load_ms === "number" ? `${o.load_ms} ms` : undefined);
    push("预热", typeof o.warmup_ms === "number" ? `${o.warmup_ms} ms` : undefined);
    push("就绪", o.loaded ? "是" : "否");
    push("RTF", o.realtime_factor);
  } else if (service.name === "gateway") {
    push("连接数", o.active);
    push("服务", o.ok ? "正常" : "异常");
  }

  if (!rows.length) return null;
  return (
    <div className="sc-kv">
      {rows.map(([k, v]) => (
        <span key={k}>
          <span className="k">{k}</span> <span className="mono">{v}</span>
        </span>
      ))}
    </div>
  );
}

export default function ServicesPage() {
  const [online, setOnline] = useState<boolean | null>(null);
  const [services, setServices] = useState<LauncherService[]>([]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<{ text: string; kind: "ok" | "err" } | null>(null);
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [engine, setEngine] = useState("cosyvoice3");
  const [progress, setProgress] = useState<Progress | null>(null);
  const [elapsed, setElapsed] = useState(0);

  // ---- 状态刷新 ----
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

  // 轮询：**启动期间也要继续刷新**。
  // 早期写法是 `if (!busy) refresh()` —— 一键启动可能等 TTS 加载 1~3 分钟，
  // 这段时间卡片一直是旧状态，用户会以为「点了没反应」。
  // 现在始终轮询，用 ref 防止慢请求叠加。
  const polling = useRef(false);
  useEffect(() => {
    refresh();
    const t = setInterval(() => {
      if (polling.current) return;
      polling.current = true;
      refresh().finally(() => {
        polling.current = false;
      });
    }, 3000);
    return () => clearInterval(t);
  }, [refresh]);

  // 进度计时器：显示「已等待 N 秒」，让长任务有确定感
  useEffect(() => {
    if (!progress) {
      setElapsed(0);
      return;
    }
    const t = setInterval(() => {
      setElapsed(Math.floor((Date.now() - progress.startedAt) / 1000));
    }, 500);
    return () => clearInterval(t);
  }, [progress]);

  useEffect(() => {
    if (!toast) return;
    // 出错信息停留更久（12s），避免没看到就消失
    const t = setTimeout(() => setToast(null), toast.kind === "err" ? 12000 : 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const setProgressSteps = (steps: string[], pct?: number) =>
    setProgress((p) =>
      p ? { ...p, steps, pct: pct ?? p.pct } : { title: "", steps, pct: 0, startedAt: Date.now() },
    );

  const withBusy = async (key: string, title: string, fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(key);
    setProgress({ title, steps: ["已提交请求…"], pct: 5, startedAt: Date.now() });
    try {
      await fn();
    } catch (e) {
      setToast({
        text: `操作异常：${e instanceof Error ? e.message : String(e)}`,
        kind: "err",
      });
    } finally {
      setBusy(null);
      await refresh();
      // 保留进度面板 2 秒让用户看到最终结果，再收起
      setTimeout(() => setProgress(null), 2000);
    }
  };

  // ---- 操作 ----
  const onStartAll = () =>
    withBusy("start-all", "正在按依赖顺序启动：TTS → ASR → LLM → 网关", async () => {
      setProgressSteps(
        [
          "服务端按顺序等待每个服务就绪…",
          "TTS 首次加载模型约 1~3 分钟，属正常",
        ],
        10,
      );

      // 启动期间持续刷新，让卡片实时变绿
      let tick = 0;
      const poll = setInterval(() => {
        tick += 1;
        refresh().then(() => {
          setProgressSteps(
            [
              `已等待 ${tick * 3} 秒，卡片状态实时更新中…`,
              "（TTS 首次加载模型较慢，属正常）",
            ],
            Math.min(90, 10 + tick * 4),
          );
        });
      }, 3000);

      const res = await launcherApi.startAll(engine);
      clearInterval(poll);

      if (!res.online) {
        setToast({ text: "启动器未运行（:50100）", kind: "err" });
        return;
      }
      const r = res.data?.result;
      if (!r) {
        setToast({ text: res.error ?? "启动请求失败", kind: "err" });
        return;
      }

      const lines = (r.results ?? []).map(
        (x) => `${x.ok ? "✓" : "✗"} ${x.name}：${x.message ?? x.action ?? ""}`,
      );
      const allSkipped = (r.results ?? []).every(
        (x) => x.action === "skip" || x.action === "probe",
      );
      setProgressSteps(lines, 100);

      if (r.ok) {
        setToast({
          text: allSkipped
            ? `全部服务本来就健康，无需启动 ✓\n${lines.join("\n")}`
            : `全链路启动完成 ✓\n${lines.join("\n")}`,
          kind: "ok",
        });
      } else {
        setToast({ text: `启动未全部成功：\n${lines.join("\n")}`, kind: "err" });
      }
    });

  const onStopAll = () =>
    withBusy("stop-all", "正在停止全部服务…", async () => {
      const res = await launcherApi.stopAll();
      const r = res.data?.result;
      if (!r) {
        setToast({ text: res.error ?? "停止请求失败", kind: "err" });
        return;
      }
      const lines = (r.results ?? []).map(
        (x) => `${x.ok ? "✓" : "✗"} ${x.name}：${x.message ?? ""}`,
      );
      setProgressSteps(lines, 100);
      setToast({
        text: r.ok ? "已停止全部服务" : `部分服务端口仍被占用（见卡片）\n${lines.join("\n")}`,
        kind: r.ok ? "ok" : "err",
      });
    });

  const onStart = (name: string) =>
    withBusy(`start:${name}`, `正在启动 ${name}…`, async () => {
      const res = await launcherApi.start(name, engine);
      const d: LauncherActionResult | undefined = res.data;
      setProgressSteps([d?.message ?? res.error ?? "已发起启动"], d?.ok ? 60 : 100);
      setToast({
        text: d?.message ?? res.error ?? (res.online ? "已发起启动" : "启动器未运行"),
        kind: d?.ok ? "ok" : "err",
      });
    });

  const onStop = (name: string) =>
    withBusy(`stop:${name}`, `正在停止 ${name}…`, async () => {
      const res = await launcherApi.stop(name);
      const d = res.data;
      setProgressSteps([d?.message ?? res.error ?? "已停止"], 100);
      setToast({
        text: d?.message ?? res.error ?? "已停止",
        kind: d?.ok ? "ok" : "err",
      });
    });

  const onLlmTest = () =>
    withBusy("llm-test", "正在测试 LLM 连通性（先代理，失败回退直连）…", async () => {
      const res = await launcherApi.llmTest();
      const d = res.data;
      if (d?.ok) {
        setProgressSteps([`连通 ✓ 路线=${d.route ?? "?"}（${d.ms ?? "?"}ms）`], 100);
        setToast({
          text: `LLM 连通 ✓ 路线=${d.route ?? "?"}（${d.ms ?? "?"}ms）`,
          kind: "ok",
        });
      } else {
        setProgressSteps([`失败：${d?.error ?? res.error ?? "未知"}`], 100);
        setToast({ text: `LLM 失败：${d?.error ?? res.error ?? "未知"}`, kind: "err" });
      }
    });

  const onLog = async (name: string) => {
    setLogs((prev) => ({ ...prev, [name]: "加载中…" }));
    const res = await launcherApi.log(name, 100);
    setLogs((prev) => ({
      ...prev,
      [name]: res.data?.log ?? res.error ?? "读取失败",
    }));
  };

  // ---- 启动器不可达：冷启动的主场景 ----
  if (online === false) {
    return (
      <>
        <PageHead title="语音链路启动" desc="一键拉起 ASR / LLM / TTS / 网关" />

        <Alert kind="warn">
          启动器未运行（:50100）。它是**独立进程**，负责真正去拉起其它服务。
        </Alert>

        <Card title="在 zjy14 目录执行（任选其一）">
          <div className="sc-cmd">.\scripts\start_web.ps1 -OpenBrowser</div>
          <div className="hint" style={{ marginTop: 10 }}>
            上面这条会一并把**本网页**和启动器都准备好（推荐）。
            只想起启动器用：
          </div>
          <div className="sc-cmd">.\scripts\start_launcher.ps1 -OpenBrowser</div>
          <div className="hint" style={{ marginTop: 10 }}>
            也可以直接双击 zjy14 目录下的 <span className="mono">一键启动网页.bat</span>。
          </div>
        </Card>

        <Card title="为什么 TTS 没起来时这一页还能用">
          <div className="hint">
            TTS 首次加载模型要十几秒，而「一键启动」必须在 TTS
            <b>还没起来</b>时就能点。所以启动器不依赖 zjy14 后端，
            本页也只是静态前端（Vite）—— 两者都不需要 :50001 在跑。
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

      <div
        style={{
          display: "flex",
          gap: 10,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <button
          className="primary"
          onClick={onStartAll}
          disabled={!!busy}
          title="按依赖顺序启动全部服务"
        >
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
          disabled={!!busy}
          title="TTS 引擎"
        >
          <option value="cosyvoice3">TTS：cosyvoice3（真模型）</option>
          <option value="stub">TTS：stub（只验链路）</option>
        </select>

        <span style={{ marginLeft: "auto" }}>
          {ready ? <Badge kind="ok">全链路就绪</Badge> : <Badge kind="warn">未就绪</Badge>}
        </span>
      </div>

      {/* 进度面板：长任务期间给确定感 */}
      {progress && (
        <div className="sc-progress">
          <div className="sc-progress-head">
            <span>{progress.title}</span>
            <span className="mono dim">{elapsed}s</span>
          </div>
          <div className="sc-progress-track">
            <div className="sc-progress-fill" style={{ width: `${progress.pct}%` }} />
          </div>
          {progress.steps.length > 0 && (
            <div className="sc-progress-steps">{progress.steps.join("\n")}</div>
          )}
        </div>
      )}

      {toast && (
        <Alert kind={toast.kind === "ok" ? "info" : "warn"}>
          <span style={{ whiteSpace: "pre-wrap" }}>{toast.text}</span>
        </Alert>
      )}

      {online === null ? (
        <Loading />
      ) : services.length === 0 ? (
        <Empty>启动器没有返回任何服务定义</Empty>
      ) : (
        <div className="grid grid-2">
          {services.map((s) => (
            <Card
              key={s.name}
              title={
                <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                  {s.label}
                  <Badge kind="dim">{kindLabel(s.kind)}</Badge>
                </span>
              }
              actions={
                <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                  {stateBadge(s)}
                </span>
              }
            >
              <div className="hint" style={{ marginBottom: 8 }}>
                {s.detail}
              </div>

              <div className="sc-kv">
                {s.port ? (
                  <span>
                    <span className="k">端口</span> <span className="mono">:{s.port}</span>
                  </span>
                ) : null}
                {s.pid ? (
                  <span>
                    <span className="k">PID</span> <span className="mono">{s.pid}</span>
                  </span>
                ) : null}
                {s.depends_on.length > 0 ? (
                  <span>
                    <span className="k">依赖</span>{" "}
                    <span className="mono">{s.depends_on.join(", ")}</span>
                  </span>
                ) : null}
                <span>
                  <span className="k">必需</span>{" "}
                  <span className="mono">{s.required ? "是" : "否"}</span>
                </span>
              </div>

              <HealthDetail service={s} />

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
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
                <pre className="sc-log">{logs[s.name]}</pre>
              )}
            </Card>
          ))}
        </div>
      )}

      <Alert kind="info">
        <b>TTS 归属：</b>本页启动 <span className="mono">zjy14</span> 的 CosyVoice3
        （WSL，<span className="mono">:50001</span>），启动网关时注入
        <span className="mono"> TTS_BASE_URL=http://127.0.0.1:50001</span>，
        因此板子听到的是 zjy14 的引擎，而不是 zjy13 自带的
        <span className="mono"> tts_server_wsl</span>（:50000）。
        <br />
        <b>ASR 归属：</b>独立服务 <span className="mono">zjy13/server/asr_service.py</span>
        （<span className="mono">:50002</span>），网关用
        <span className="mono"> --asr-url</span> 调用它，本机不再重复加载 FunASR。
        <br />
        <b>兜底入口：</b>启动器自带一个不依赖 node 的控制台
        <span className="mono"> http://127.0.0.1:50100/</span>，两者是同一套 API。
      </Alert>

      {error && <ErrorBox message={error} />}
    </>
  );
}