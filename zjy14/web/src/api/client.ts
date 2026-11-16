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

  /** 流式合成：边收边回调，返回最终统计。
   *
   * 协议（与 zjy13 一致）：每块 = 4 字节大端 uint32 长度 + PCM16LE；
   * 长度 0 表示结束。
   *
   * 为什么不用 EventSource：
   *   音频是二进制，SSE 只支持文本，Base64 会多 33% 体积。
   *   用 fetch + ReadableStream 直接读二进制更高效。
   *
   * @param body 合成请求
   * @param onChunk 每收到一块 PCM 时回调（已去掉长度前缀）
   * @param signal 用于取消
   */
  async synthesizeStream(
    body: SynthesisRequest,
    onChunk: (pcm: ArrayBuffer, index: number) => void,
    signal?: AbortSignal,
  ): Promise<StreamSummary> {
    const started = performance.now();
    const res = await fetch(BASE + "/synthesize_stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok) {
      await parseError(res);
    }

    const sampleRate = Number(res.headers.get("x-sample-rate") ?? 24000);
    const requestId = res.headers.get("x-request-id") ?? "";
    const reader = res.body?.getReader();
    if (!reader) {
      throw new ApiError("响应不支持流式读取", "no_stream", 0);
    }

    let buf = new Uint8Array(0);
    let totalBytes = 0;
    let chunks = 0;
    let firstChunkMs: number | null = null;
    let gotEnd = false;

    const appendBuf = (src: Uint8Array) => {
      const merged = new Uint8Array(buf.length + src.length);
      merged.set(buf, 0);
      merged.set(src, buf.length);
      buf = merged;
    };

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) appendBuf(value);

        // 尽可能多地解析完整块
        for (;;) {
          if (buf.length < 4) break;
          const view = new DataView(buf.buffer, buf.byteOffset, 4);
          const len = view.getUint32(0, false); // 大端
          if (buf.length < 4 + len) break;

          const pcm = buf.slice(4, 4 + len);
          buf = buf.slice(4 + len);

          if (len === 0) {
            gotEnd = true;
            break;
          }
          if (firstChunkMs === null) {
            firstChunkMs = performance.now() - started;
          }
          totalBytes += len;
          onChunk(pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + len), chunks);
          chunks += 1;
        }
        if (gotEnd) break;
      }
    } finally {
      reader.releaseLock();
    }

    if (!gotEnd) {
      // 服务端中途断开时不发结束标记，这里必须报错而不是静默接受
      throw new ApiError(
        "流被截断（未收到结束标记），音频可能不完整",
        "stream_truncated",
        0,
      );
    }

    return {
      sampleRate,
      requestId,
      chunks,
      totalBytes,
      durationS: totalBytes / 2 / sampleRate,
      firstChunkMs: firstChunkMs ?? -1,
      totalMs: performance.now() - started,
    };
  },
};

/** 流式合成统计。 */
export interface StreamSummary {
  sampleRate: number;
  requestId: string;
  chunks: number;
  totalBytes: number;
  durationS: number;
  firstChunkMs: number;
  totalMs: number;
}

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
