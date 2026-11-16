/** API 客户端。
 *
 * 设计原则：
 * - 统一错误处理：把后端返回的 {code, message, detail} 转成可读异常
 * - 不静默吞错：所有失败都抛 ApiError，由页面决定怎么展示
 * - 合成接口单独处理：返回音频 Blob + 响应头元数据
 */

import type {
  ApiErrorBody,
  Character,
  GpuInfo,
  HealthResponse,
  QualityReport,
  Speaker,
  SynthesisMeta,
  SynthesisRequest,
  SystemStatus,
} from "./types";

const BASE = ""; // 同源，Vite dev 用 proxy

export class ApiError extends Error {
  code: string;
  status: number;
  detail?: Record<string, unknown>;

  constructor(message: string, code: string, status: number, detail?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
}

async function parseError(res: Response): Promise<never> {
  let body: Partial<ApiErrorBody> = {};
  try {
    body = (await res.json()) as ApiErrorBody;
  } catch {
    // 响应不是 JSON（例如 FastAPI 的 422 结构不同）
    body = { code: `http_${res.status}`, message: res.statusText || "请求失败" };
  }
  throw new ApiError(
    body.message ?? `HTTP ${res.status}`,
    body.code ?? `http_${res.status}`,
    res.status,
    body.detail,
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    await parseError(res);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------- 系统

export const systemApi = {
  health: () => request<HealthResponse>("/health"),
  status: () => request<SystemStatus>("/api/v1/system/status"),
  gpu: () => request<GpuInfo>("/api/v1/system/gpu"),
};

// ---------------------------------------------------------------- 角色

export const characterApi = {
  list: () => request<Character[]>("/api/v1/characters"),

  get: (id: string) => request<Character>(`/api/v1/characters/${encodeURIComponent(id)}`),

  create: (body: Partial<Character>) =>
    request<Character>("/api/v1/characters", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  update: (id: string, body: Partial<Character>) =>
    request<Character>(`/api/v1/characters/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  remove: (id: string) =>
    request<void>(`/api/v1/characters/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

// ---------------------------------------------------------------- 声音

export const speakerApi = {
  list: () => request<Speaker[]>("/api/v1/speakers"),

  get: (id: string) => request<Speaker>(`/api/v1/speakers/${encodeURIComponent(id)}`),

  create: (body: {
    speaker_id: string;
    display_name?: string;
    audio_path: string;
    transcript: string;
    consent_status?: string;
    source?: string;
  }) =>
    request<Speaker>("/api/v1/speakers", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  update: (id: string, body: Partial<Speaker>) =>
    request<Speaker>(`/api/v1/speakers/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  remove: (id: string) =>
    request<void>(`/api/v1/speakers/${encodeURIComponent(id)}`, { method: "DELETE" }),

  validate: (id: string) =>
    request<QualityReport>(`/api/v1/speakers/${encodeURIComponent(id)}/validate`, {
      method: "POST",
    }),
};

// ---------------------------------------------------------------- 合成

export interface SynthesisResult {
  blob: Blob;
  meta: SynthesisMeta;
}

export const synthesisApi = {
  /** 合成语音。返回音频 Blob 与从响应头解析的元数据。 */
  async synthesize(body: SynthesisRequest): Promise<SynthesisResult> {
    const res = await fetch(BASE + "/synthesize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      await parseError(res);
    }
    const blob = await res.blob();
    const h = res.headers;
    const meta: SynthesisMeta = {
      sample_rate: Number(h.get("x-sample-rate") ?? 0),
      duration: Number(h.get("x-duration") ?? 0),
      total_ms: Number(h.get("x-total-ms") ?? 0),
      rtf: Number(h.get("x-rtf") ?? 0),
      engine: h.get("x-engine") ?? "",
      voice_id: h.get("x-voice-id") ?? "",
      request_id: h.get("x-request-id") ?? "",
    };
    return { blob, meta };
  },
};

/** 把 ApiError 转成可展示的文本（含 detail 摘要）。 */
export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    let msg = e.message;
    if (e.detail && Object.keys(e.detail).length > 0) {
      const d = e.detail;
      if (Array.isArray(d.blocking_issues) && d.blocking_issues.length > 0) {
        msg += `（${(d.blocking_issues as string[]).join(", ")}）`;
      } else if (Array.isArray(d.issues) && d.issues.length > 0) {
        msg += `：${(d.issues as string[]).slice(0, 2).join("；")}`;
      }
    }
    return msg;
  }
  if (e instanceof Error) return e.message;
  return String(e);
}
