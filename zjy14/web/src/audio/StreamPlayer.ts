/** 流式 PCM 播放器。
 *
 * 为什么需要它：
 *   非流式要等整段音频合成完（长句实测 5.4s）才能播。
 *   流式首块约 1.4s 就能到，边收边播可以把「首声时间」从 5.4s 降到 1.4s。
 *
 * 实现要点：
 *   1. 用 Web Audio API 的 AudioBufferSourceNode 逐块调度
 *   2. 每块按「上一块结束时间」精确排期，避免重叠或空隙
 *   3. 输入是 PCM16LE 单声道，需转成 Float32 [-1,1]
 *   4. 支持 stop() 立即停止（用户取消/重新合成）
 *
 * 为什么不用 <audio> 标签：
 *   它需要完整文件或至少可 seek 的流，无法处理「未知总长的裸 PCM 块」。
 */

export interface StreamPlayerStats {
  scheduledChunks: number;
  scheduledBytes: number;
  firstChunkAt: number | null;
  /** 从开始到首块排期的毫秒数 */
  firstChunkMs: number | null;
}

export class StreamPlayer {
  private ctx: AudioContext | null = null;
  private sources: AudioBufferSourceNode[] = [];
  private nextStartTime = 0;
  private sampleRate: number;
  private startedAt = 0;
  private stats: StreamPlayerStats = {
    scheduledChunks: 0,
    scheduledBytes: 0,
    firstChunkAt: null,
    firstChunkMs: null,
  };

  constructor(sampleRate = 24000) {
    this.sampleRate = sampleRate;
  }

  /** 开始新的播放会话。会重置内部状态。
   *
   * 注意：浏览器要求 AudioContext 必须在用户手势（点击等）中创建/恢复，
   * 否则会是 suspended 状态，音频不会响。
   * 本方法由按钮点击触发，但首次调用仍可能 suspended，故主动 resume。
   */
  start(): void {
    this.stop();
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    this.ctx = new Ctor();
    // 若被浏览器策略挂起，主动恢复。失败不致命（后续 push 时会再试）
    if (this.ctx.state === "suspended") {
      void this.ctx.resume();
    }
    this.nextStartTime = 0;
    this.startedAt = performance.now();
    this.stats = {
      scheduledChunks: 0,
      scheduledBytes: 0,
      firstChunkAt: null,
      firstChunkMs: null,
    };
  }

  /** 排入一块 PCM16LE 数据。 */
  push(pcm: ArrayBuffer): void {
    if (!this.ctx) return;
    if (pcm.byteLength === 0) return;

    // 某些浏览器首次 push 时仍是 suspended，再试一次恢复
    if (this.ctx.state === "suspended") {
      void this.ctx.resume();
    }

    const int16 = new Int16Array(pcm);
    const frames = int16.length;
    if (frames === 0) return;

    // PCM16 -> Float32
    const buffer = this.ctx.createBuffer(1, frames, this.sampleRate);
    const ch = buffer.getChannelData(0);
    for (let i = 0; i < frames; i++) {
      ch[i] = int16[i]! / 32768;
    }

    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.ctx.destination);

    // 首块立即播，后续按上一块结束时间排期
    const now = this.ctx.currentTime;
    const startAt = this.nextStartTime > now ? this.nextStartTime : now + 0.02;
    src.start(startAt);
    this.nextStartTime = startAt + buffer.duration;

    this.sources.push(src);
    src.onended = () => {
      const i = this.sources.indexOf(src);
      if (i >= 0) this.sources.splice(i, 1);
    };

    if (this.stats.firstChunkAt === null) {
      this.stats.firstChunkAt = performance.now();
      this.stats.firstChunkMs = this.stats.firstChunkAt - this.startedAt;
    }
    this.stats.scheduledChunks += 1;
    this.stats.scheduledBytes += pcm.byteLength;
  }

  /** 立即停止所有已排期的音频。 */
  stop(): void {
    for (const s of this.sources) {
      try {
        s.stop();
      } catch {
        // 已停止的 source 再 stop 会抛，忽略
      }
    }
    this.sources = [];
    this.nextStartTime = 0;
    if (this.ctx && this.ctx.state !== "closed") {
      void this.ctx.close();
    }
    this.ctx = null;
  }

  /** 返回统计快照。 */
  snapshot(): StreamPlayerStats & { playedS: number } {
    return {
      ...this.stats,
      playedS: this.stats.scheduledBytes / 2 / this.sampleRate,
    };
  }

  get isActive(): boolean {
    return this.ctx !== null;
  }
}
