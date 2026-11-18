/** API 类型定义。与后端 schemas 一一对应。 */

export type EngineKind = "stub" | "cosyvoice3";

/** /health */
export interface HealthResponse {
  ok: boolean;
  engine: string;
  sample_rate: number;
  device: string;
  gpu: string;
  torch: string;
  load_s: number;
  warmup_s: number;
  voices: { voice_id: string; is_builtin: boolean }[];
  peak_vram_mb: number | null;
  diagnostics?: {
    model_dir: string;
    text_frontend: string;
    voice_count: number;
    hop_len_resets: number;
    ratio_warnings: number;
    precision: string;
    peak_vram_mb: number;
  };
}

/** /api/v1/system/status */
export interface SystemStatus {
  ok: boolean;
  version: string;
  uptime_s: number;
  engine: {
    kind: string;
    ready: boolean;
    sample_rate: number;
    device: string;
    gpu: string;
    torch: string;
    voices: string[];
  };
  config: {
    engine: string;
    device: string;
    precision: string;
    max_concurrent_requests: number;
    request_timeout_s: number;
    speaker_cache_enabled: boolean;
    speaker_cache_max_items: number;
    memory_safety_margin_mb: number;
    token_hop_len_reset: boolean;
    text_frontend: string;
    default_voice_id: string;
    min_text_ratio: number;
    model_dir: string;
    host: string;
    port: number;
    auth_token_set: boolean;
    is_loopback: boolean;
  };
  config_sources: {
    config_path: string;
    config_exists: boolean;
    env_overrides: string[];
    consumed_field_count: number;
  };
  paths: Record<string, string>;
  db: { tables: string[]; journal_mode: string; foreign_keys: boolean };
}

/** /api/v1/system/gpu */
export interface GpuInfo {
  ok: boolean;
  reason?: string;
  cuda_available: boolean;
  torch?: string;
  cuda_runtime?: string;
  device_name?: string;
  capability?: number[];
  total_mb?: number;
  free_mb?: number;
  used_mb?: number;
  allocated_mb?: number;
  peak_allocated_mb?: number;
  multi_processor_count?: number;
  arch_list?: string[];
}

/** 角色 */
export interface Character {
  character_id: string;
  display_name: string;
  description: string;
  personality: string;
  speaking_habits: string;
  default_emotion: string;
  default_style: string;
  speaker_id: string | null;
  language: string;
  allowed_emotions: string[];
  voice_instruction_template: string;
  model_id: string;
  model_version: string;
  config_version: string;
  created_at: string;
  updated_at: string;
}

/** 质量检测中的一条问题 */
export interface QualityIssue {
  level: "blocking" | "warning" | "info";
  code: string;
  message: string;
}

/** 质量报告 */
export interface QualityReport {
  path: string;
  ok: boolean;
  score: number;
  duration_s: number;
  sample_rate: number;
  channels: number;
  rms_dbfs: number;
  peak: number;
  snr_db: number;
  silence_ratio: number;
  clip_ratio: number;
  issues: QualityIssue[];
  summary?: string;
}

/** 声音档案 */
export interface Speaker {
  speaker_id: string;
  display_name: string;
  audio_path: string;
  transcript: string;
  sample_rate: number;
  channels: number;
  duration_s: number;
  quality_report: Partial<QualityReport>;
  consent_status: string;
  source: string;
  feature_cache_key: string;
  model_version: string;
  preprocessing_version: string;
  created_at: string;
  updated_at: string;
}

/** 合成请求 */
export interface SynthesisRequest {
  text: string;
  voice_id?: string;
  speed?: number;
  volume?: number;
  pause_profile?: string;
  instruction?: string | null;
  emotion?: string | null;
  intensity?: number | null;
  style?: string | null;
}

/** 合成结果的元信息（从响应头解析） */
export interface SynthesisMeta {
  sample_rate: number;
  duration: number;
  total_ms: number;
  rtf: number;
  engine: string;
  voice_id: string;
  request_id: string;
}

/** API 错误 */
export interface ApiErrorBody {
  code: string;
  message: string;
  detail?: Record<string, unknown>;
}

/** 一条合成记录（GET /api/v1/history） */
export interface SynthesisRecord {
  id: number;
  request_id: string;
  text: string;
  voice_id: string;
  character_id: string | null;
  speaker_id: string | null;
  emotion: string;
  style: string;
  speed: number;
  instruction: string;
  sample_rate: number;
  duration_s: number;
  total_ms: number;
  first_audio_ms: number | null;
  rtf: number;
  peak_vram_mb: number | null;
  engine: string;
  error_code: string;
  created_at: string;
}

/** 合成记录分页结果 */
export interface HistoryPage {
  items: SynthesisRecord[];
  next_cursor: string | null;
  has_more: boolean;
  count: number;
}

/** 合成记录统计 */
export interface HistoryStats {
  total: number;
  success: number;
  failed: number;
  success_rate: number;
  total_audio_s: number;
  total_synth_s: number;
  rtf_median: number;
  rtf_mean: number;
  rtf_min: number;
  rtf_max: number;
  by_voice: Record<string, number>;
  by_error: Record<string, number>;
  first_at: string | null;
  last_at: string | null;
}

/** 合成记录查询参数 */
export interface HistoryQuery {
  voice_id?: string;
  character_id?: string;
  emotion?: string;
  only_failed?: boolean;
  only_success?: boolean;
  text_contains?: string;
  text_search?: string;
  /** time（时间倒序，默认）/ relevance（相关度，仅 FTS 有效） */
  order_by?: "time" | "relevance";
  since?: string;
  until?: string;
  limit?: number;
  cursor?: string;
}

/** 一条清理审计记录 */
export interface CleanupRecord {
  kind: string;
  cutoff: string;
  removed: number;
  triggered_by: string;
  /** 额外信息，如被删除的文件名列表 */
  detail: Record<string, unknown>;
  created_at: string;
}

/** 一条日志 */
export interface LogEntry {
  ts: string;
  level: string;
  logger: string;
  event: string;
  request_id?: string;
  line_no: number;
  exc?: string;
  fields?: Record<string, unknown>;
  /** 来源文件名（跨文件搜索时用于区分是哪天的日志） */
  source_file?: string;
}

/** 日志查询结果 */
export interface LogPage {
  entries: LogEntry[];
  count: number;
  scanned: number;
  bad_lines: number;
  has_more: boolean;
  path: string;
  file_size: number;
}

/** 日志文件信息 */
export interface LogFile {
  name: string;
  size: number;
  modified_at: string;
  is_current: boolean;
}

/** 日志查询参数 */
export interface LogQuery {
  limit?: number;
  levels?: string[];
  request_id?: string;
  keyword?: string;
  logger_name?: string;
  only_errors?: boolean;
  file_name?: string;
}

// ----------------------------------------------------------------------
// 设备状态监控（数据来自 zjy13 的 /api/devices）
// ----------------------------------------------------------------------

/** 对话状态（与 zjy13 server/ws/states.py 的 State 枚举一致） */
export type DeviceState =
  | "idle"
  | "recording"
  | "asr_processing"
  | "llm_processing"
  | "tts_processing"
  | "filler"
  | "playing"
  | "error";

/** 本轮各阶段耗时（毫秒）。未完成的阶段为 null。 */
export interface DeviceTimings {
  asr_ms: number | null;
  llm_first_ms: number | null;
  llm_total_ms: number | null;
  tts_first_ms: number | null;
  playback_ms: number | null;
}

/** 音频下发进度 */
export interface DeviceAudioProgress {
  expect_bytes: number;
  expect_frames: number;
  sent_bytes: number;
  sent_frames: number;
  /** 0–1；流式（expect_frames=0）时为 null */
  progress: number | null;
  /** 流式模式：总长未知 */
  streaming: boolean;
}

/** 一条错误记录 */
export interface DeviceError {
  code: string;
  message: string;
  at: string;
  turn_id?: string | null;
}

/** 单个设备（一块板子）的运行时状态 */
export interface DeviceClient {
  session_id: string;
  device_id: string;
  peer: string;
  hello_done: boolean;
  connected_s: number;

  state: DeviceState | string;
  state_elapsed_ms: number;
  turn_id: string;
  turn_seq: number;
  is_busy: boolean;

  last_heartbeat_s: number;
  heartbeat_ok: boolean;

  audio: DeviceAudioProgress;

  filler_session_open: boolean;
  dialogue_turns: number;
  turns_done: number;

  last_asr_text: string;
  last_reply_text: string;

  timings: DeviceTimings;

  last_error: string | null;
  errors_recent: DeviceError[];

  /** 快照生成失败时的占位字段 */
  error?: string;
}

/** 服务端信息 */
export interface DeviceServerInfo {
  host: string;
  port: number;
  character_id: string;
  character_name: string;
  uptime_s: number;
  max_connections: number;
  access_token_required: boolean;
  audio_in_sample_rate: number;
  audio_in_channels: number;
  audio_out_sample_rate: number;
  protocol_version: number;
  filler_available: boolean;
  mean_latency_s: number | null;
}

/** 计数器 */
export interface DeviceCounters {
  active: number;
  total_connections: number;
  rejected_connections: number;
  total_turns: number;
  total_errors: number;
}

/** GET /api/devices 的完整快照 */
export interface DeviceSnapshot {
  server: DeviceServerInfo;
  clients: DeviceClient[];
  counters: DeviceCounters;
}

/** 监控服务配置（前端侧，不来自后端） */
export interface MonitorConfig {
  /** zjy13 监控端点基址，如 http://127.0.0.1:8765 */
  baseUrl: string;
  /** 轮询间隔（毫秒） */
  intervalMs: number;
}

/** 官方情绪标签（仅元数据） */
export const EMOTIONS = [
  { value: "neutral", label: "中性" },
  { value: "happy", label: "开心" },
  { value: "sad", label: "难过" },
  { value: "angry", label: "生气" },
  { value: "comforting", label: "安慰" },
  { value: "playful", label: "俏皮" },
] as const;

/** 停顿档位 */
export const PAUSE_PROFILES = [
  { value: "none", label: "不分句", ms: 0 },
  { value: "tight", label: "紧凑", ms: 80 },
  { value: "natural", label: "自然", ms: 180 },
  { value: "relaxed", label: "舒缓", ms: 350 },
  { value: "dramatic", label: "戏剧化", ms: 600 },
] as const;
