/** 语音链路启动器（launcher）的 API 客户端。
 *
 * 为什么单独一个文件而不是塞进 client.ts：
 *   `client.ts` 打交道的对象是 zjy14 自身的 FastAPI（:50001），
 *   同源 + Vite 代理；而启动器是**另一个进程**（:50100，
 *   只用标准库的独立服务），且它的存在与否本身就是要展示的信息。
 *
 * 关键设计：启动器可能根本没运行（这正是「要一键启动」时的常态）。
 *   所以这里**不抛异常给页面**，而是统一返回 {online:false, error}，
 *   让页面显示「启动器未运行 → 请先运行 scripts/start_launcher.ps1」。
 */

export const LAUNCHER_BASE = ""; // 同源：由 Vite 代理 /launcher → :50100

export interface LauncherService {
  name: string;
  label: string;
  kind: "win" | "wsl" | "probe";
  port: number | null;
  required: boolean;
  depends_on: string[];
  running: boolean;
  managed: boolean;
  healthy: boolean;
  pid: number | null;
  detail: string;
  health: unknown;
  log_path: string;
}

export interface LauncherStatus {
  ok: boolean;
  ready: boolean;
  services: LauncherService[];
  timestamp: string;
}

export interface LauncherActionResult {
  ok: boolean;
  name?: string;
  action?: string;
  message?: string;
  pid?: number | null;
  log_path?: string;
  log_tail?: string;
  error?: string;
}

export interface StartAllResult {
  ok: boolean;
  failed: string | null;
  failed_required: string[];
  results: LauncherActionResult[];
}

/** 统一包装：把「启动器不可达」变成可展示的结果，而不是异常。 */
async function call<T>(
  path: string,
  init?: RequestInit,
): Promise<{ online: boolean; data?: T; error?: string }> {
  try {
    const res = await fetch(LAUNCHER_BASE + path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
    if (!res.ok) {
      return { online: true, error: `HTTP ${res.status}` };
    }
    return { online: true, data: (await res.json()) as T };
  } catch (e) {
    return {
      online: false,
      error: e instanceof Error ? e.message : "无法连接启动器（:50100）",
    };
  }
}

export const launcherApi = {
  status: () => call<LauncherStatus>("/launcher/api/status"),

  specs: () => call<{ ok: boolean; engine: string; services: LauncherService[] }>(
    "/launcher/api/specs",
  ),

  start: (name: string, engine?: string) =>
    call<LauncherActionResult>(`/launcher/api/start/${name}`, {
      method: "POST",
      body: JSON.stringify({ engine }),
    }),

  stop: (name: string) =>
    call<LauncherActionResult>(`/launcher/api/stop/${name}`, { method: "POST", body: "{}" }),

  startAll: (engine?: string) =>
    call<{ ok: boolean; result: StartAllResult }>("/launcher/api/start-all", {
      method: "POST",
      body: JSON.stringify({ engine }),
    }),

  stopAll: () =>
    call<{ ok: boolean; result: StartAllResult }>("/launcher/api/stop-all", {
      method: "POST",
      body: "{}",
    }),

  llmTest: () =>
    call<{ ok: boolean; route?: string; ms?: number; error?: string; status?: number }>(
      "/launcher/api/llm/test",
      { method: "POST", body: "{}" },
    ),

  log: (name: string, lines = 80) =>
    call<{ ok: boolean; name: string; log: string }>(
      `/launcher/api/log/${name}?lines=${lines}`,
    ),
};