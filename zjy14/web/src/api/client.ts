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
  CleanupRecord,
  DeviceSnapshot,
  GpuInfo,
  HealthResponse,
  HistoryPage,
  HistoryQuery,
  HistoryStats,
  LogFile,
  LogPage,
  LogQuery,
  QualityReport,
  Speaker,
  SynthesisMeta,
  SynthesisRecord,
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

/** 把查询对象转成 URL 查询串（跳过 undefined / 空数组）。 */
function toQueryString(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) {
      // 数组参数重复传（后端用 list[str] 接收）
      for (const item of v) {
        if (item !== undefined && item !== null && item !== "") {
          sp.append(k, String(item));
        }
      }
    } else if (typeof v === "boolean") {
      // 只传 true，避免 URL 冗长
      if (v) sp.append(k, "true");
    } else {
      sp.append(k, String(v));
    }
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

/** 合成记录查询。 */
export const historyApi = {
  /** 分页查询合成记录。 */
  async list(q: HistoryQuery = {}): Promise<HistoryPage> {
    const res = await fetch(BASE + "/api/v1/history" + toQueryString({ ...q }));
    if (!res.ok) await parseError(res);
    return (await res.json()) as HistoryPage;
  },

  /** 统计（不受分页影响）。 */
  async stats(q: Omit<HistoryQuery, "limit" | "cursor"> = {}): Promise<HistoryStats> {
    const res = await fetch(BASE + "/api/v1/history/stats" + toQueryString({ ...q }));
    if (!res.ok) await parseError(res);
    return (await res.json()) as HistoryStats;
  },

  /** 单条详情。 */
  async get(id: number): Promise<SynthesisRecord> {
    const res = await fetch(BASE + `/api/v1/history/${id}`);
    if (!res.ok) await parseError(res);
    return (await res.json()) as SynthesisRecord;
  },

  /** 按 request_id 查（可能多条）。 */
  async byRequest(requestId: string): Promise<SynthesisRecord[]> {
    const res = await fetch(
      BASE + `/api/v1/history/by-request/${encodeURIComponent(requestId)}`,
    );
    if (!res.ok) await parseError(res);
    return (await res.json()) as SynthesisRecord[];
  },

  /** 清理审计记录。 */
  async cleanupHistory(limit = 20): Promise<CleanupRecord[]> {
    const res = await fetch(
      BASE + "/api/v1/history/cleanup-history" + toQueryString({ limit }),
    );
    if (!res.ok) await parseError(res);
    return (await res.json()) as CleanupRecord[];
  },

  /** 导出查询结果为文件并触发浏览器下载。
   *
   * 后端是流式响应，会先给出 ``x-export-total``（预计总行数）。
   * 这里把总数回调出去，让页面能显示「正在导出 N 条」。
   *
   * 为什么不用 XHR 的 onprogress：
   *   fetch 的 ReadableStream 在浏览器里拿不到总字节数
   *   （content-length 未知，因为是流式生成），
   *   但后端知道总行数，用它做进度提示更准确。
   */
  async exportFile(
    q: HistoryQuery,
    fmt: "csv" | "json" = "csv",
    onProgress?: (info: { total: number; limit: number }) => void,
  ): Promise<void> {
    const res = await fetch(
      BASE + "/api/v1/history/export" + toQueryString({ ...q, fmt }),
    );
    if (!res.ok) await parseError(res);

    const total = Number(res.headers.get("x-export-total") ?? 0);
    const limit = Number(res.headers.get("x-export-limit") ?? 0);
    onProgress?.({ total, limit });

    // 从 content-disposition 取文件名，取不到就用时间戳兜底
    const cd = res.headers.get("content-disposition") ?? "";
    const m = /filename="?([^";]+)"?/.exec(cd);
    const filename =
      m?.[1] ?? `synthesis-history-${new Date().toISOString().slice(0, 10)}.${fmt}`;

    // 流式读取并累积，避免一次性占大内存
    const reader = res.body?.getReader();
    const parts: Uint8Array[] = [];
    if (reader) {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) parts.push(value);
      }
    } else {
      parts.push(new Uint8Array(await res.arrayBuffer()));
    }

    const blob = new Blob(parts as BlobPart[], {
      type: res.headers.get("content-type") ?? "application/octet-stream",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },

  /** 清理指定时间之前的记录。 */
  async purge(before: string): Promise<void> {
    const res = await fetch(BASE + "/api/v1/history" + toQueryString({ before }), {
      method: "DELETE",
    });
    if (!res.ok) await parseError(res);
  },
};

/** 日志查询。 */
export const logsApi = {
  /** 查询日志（最新在前）。 */
  async query(q: LogQuery = {}): Promise<LogPage> {
    const res = await fetch(BASE + "/api/v1/logs" + toQueryString({ ...q }));
    if (!res.ok) await parseError(res);
    return (await res.json()) as LogPage;
  },

  /** 日志文件列表（含轮转归档）。 */
  async files(): Promise<LogFile[]> {
    const res = await fetch(BASE + "/api/v1/logs/files");
    if (!res.ok) await parseError(res);
    return (await res.json()) as LogFile[];
  },
};

// ----------------------------------------------------------------------
// 设备状态监控
// ----------------------------------------------------------------------
//
// 数据来自 **zjy13 的 WebSocket 服务**，不是 zjy14。
// zjy13 在 :8765 上同时提供 WS 与只读 HTTP（/api/devices）。
// 因为端口不同，属于跨域请求 —— zjy13 侧已返回
// Access-Control-Allow-Origin: *，所以浏览器可直接读。
//
// 基址可通过 VITE_ZJY13_BASE 覆盖（默认 http://127.0.0.1:8765）。

const ZJY13_BASE =
  (import.meta.env.VITE_ZJY13_BASE as string | undefined) ??
  "http://127.0.0.1:8765";

export const deviceApi = {
  /** 拉取一次设备状态快照。 */
  async snapshot(baseUrl = ZJY13_BASE, signal?: AbortSignal): Promise<DeviceSnapshot> {
    const res = await fetch(`${baseUrl}/api/devices`, { signal });
    if (!res.ok) {
      throw new ApiError(
        `设备监控服务返回 ${res.status}`,
        `http_${res.status}`,
        res.status,
      );
    }
    return (await res.json()) as DeviceSnapshot;
  },

  /** 健康检查（极简，用于判断监控服务是否在跑）。 */
  async health(baseUrl = ZJY13_BASE, signal?: AbortSignal): Promise<{ ok: boolean; active: number }> {
    const res = await fetch(`${baseUrl}/api/devices/health`, { signal });
    if (!res.ok) {
      throw new ApiError(`健康检查失败 ${res.status}`, `http_${res.status}`, res.status);
    }
    return (await res.json()) as { ok: boolean; active: number };
  },

  /** 当前使用的基址（供 UI 显示）。 */
  baseUrl: ZJY13_BASE,
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
