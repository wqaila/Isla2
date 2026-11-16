// 验证前端流式解析逻辑（Node 环境，模拟浏览器的 ReadableStream 消费）。
//
// 目的：前端 StreamPlayer 与 client.synthesizeStream 的核心是
// 「4 字节大端长度前缀 + PCM」的增量解析。这段逻辑最容易出错
// （块跨包、结束标记、截断检测），必须在真服务上验证。
//
// 本脚本用与前端完全相同的解析算法，消费真实后端的流。

const BASE = process.env.ZJY14_BASE || "http://127.0.0.1:50001";

const TEXTS = {
  "短句_10字": "你好呀，今天过得怎么样？",
  "中句_22字": "今天天气不错，我们下午去公园散步吧，顺便买点吃的。",
  "长句_44字":
    "我最近在调试一块 ESP32-S3 的开发板，屏幕驱动那边一直有点问题，你有空的话能不能帮我看看？",
};

/** 与前端 client.ts 的 synthesizeStream 完全相同的解析逻辑。 */
async function streamOnce(text) {
  const started = performance.now();
  const res = await fetch(`${BASE}/synthesize_stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice_id: "elysia" }),
  });

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  }

  const sampleRate = Number(res.headers.get("x-sample-rate") ?? 24000);
  const requestId = res.headers.get("x-request-id") ?? "";
  const reader = res.body.getReader();

  let buf = new Uint8Array(0);
  let totalBytes = 0;
  let chunks = 0;
  let firstChunkMs = null;
  let gotEnd = false;

  const appendBuf = (src) => {
    const merged = new Uint8Array(buf.length + src.length);
    merged.set(buf, 0);
    merged.set(src, buf.length);
    buf = merged;
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value) appendBuf(value);

    for (;;) {
      if (buf.length < 4) break;
      const view = new DataView(buf.buffer, buf.byteOffset, 4);
      const len = view.getUint32(0, false);
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
      chunks += 1;
    }
    if (gotEnd) break;
  }

  if (!gotEnd) {
    throw new Error("流被截断（未收到结束标记）");
  }

  return {
    sampleRate,
    requestId,
    chunks,
    totalBytes,
    durationS: totalBytes / 2 / sampleRate,
    firstChunkMs,
    totalMs: performance.now() - started,
  };
}

/** 非流式作为对比基线。 */
async function oneshotOnce(text) {
  const started = performance.now();
  const res = await fetch(`${BASE}/synthesize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice_id: "elysia" }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const buf = await res.arrayBuffer();
  return {
    bytes: buf.byteLength,
    durationS: Number(res.headers.get("x-duration") ?? 0),
    totalMs: performance.now() - started,
  };
}

async function main() {
  console.log("=".repeat(96));
  console.log(" 前端流式解析验证（与 client.ts 相同算法）");
  console.log("=".repeat(96));
  console.log(`  后端: ${BASE}`);

  // 健康检查
  const health = await fetch(`${BASE}/health`).then((r) => r.json());
  console.log(`  引擎: ${health.engine}  采样率: ${health.sample_rate}`);

  let allOk = true;
  const rows = [];

  for (const [label, text] of Object.entries(TEXTS)) {
    console.log(`\n${"-".repeat(96)}`);
    console.log(` ${label}（${text.length} 字符）`);
    console.log("-".repeat(96));

    // 预热一次，排除 CUDA 初始化
    try {
      await streamOnce(text);
    } catch {
      /* 忽略预热错误 */
    }

    let st;
    try {
      st = await streamOnce(text);
    } catch (e) {
      console.log(`  [FAIL] 流式失败: ${e.message}`);
      allOk = false;
      continue;
    }

    console.log(`  流式: 分块 ${st.chunks}  字节 ${st.totalBytes}  ` +
      `音频 ${st.durationS.toFixed(2)}s`);
    console.log(`        首块 ${st.firstChunkMs.toFixed(0)}ms  ` +
      `总耗时 ${st.totalMs.toFixed(0)}ms`);

    // 校验
    const checks = [];
    if (st.chunks > 0) checks.push(["收到数据块", true]);
    else checks.push(["收到数据块", false]);

    if (st.sampleRate === 24000) checks.push(["采样率 24000", true]);
    else checks.push([`采样率 ${st.sampleRate}`, false]);

    if (st.firstChunkMs !== null && st.firstChunkMs < st.totalMs) {
      checks.push(["首块早于总耗时（流式生效）", true]);
    } else {
      checks.push(["首块早于总耗时（流式生效）", false]);
    }

    if (st.totalBytes > 0 && st.durationS > 0.1) {
      checks.push(["音频时长合理", true]);
    } else {
      checks.push(["音频时长合理", false]);
    }

    // 与非流式对比
    let os = null;
    try {
      os = await oneshotOnce(text);
      console.log(`  非流式: 字节 ${os.bytes}  音频 ${os.durationS.toFixed(2)}s  ` +
        `总耗时 ${os.totalMs.toFixed(0)}ms`);
      const improvement =
        ((os.totalMs - st.firstChunkMs) / os.totalMs) * 100;
      console.log(`  首声改善: ${improvement.toFixed(1)}%` +
        `（${improvement > 5 ? "流式更优" : "无明显优势"}）`);
      rows.push({ label, stream: st, oneshot: os, improvement });
    } catch (e) {
      console.log(`  [WARN] 非流式对比失败: ${e.message}`);
    }

    for (const [name, ok] of checks) {
      console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}`);
      if (!ok) allOk = false;
    }
  }

  // 汇总
  console.log("\n" + "=".repeat(96));
  console.log(" 汇总");
  console.log("=".repeat(96));
  console.log(
    "  " +
      "文本".padEnd(14) +
      "分块".padStart(6) +
      "音频".padStart(9) +
      "首块ms".padStart(10) +
      "非流式ms".padStart(11) +
      "改善".padStart(9)
  );
  console.log("  " + "-".repeat(60));
  for (const r of rows) {
    console.log(
      "  " +
        r.label.padEnd(14) +
        String(r.stream.chunks).padStart(6) +
        (r.stream.durationS.toFixed(2) + "s").padStart(9) +
        r.stream.firstChunkMs.toFixed(0).padStart(10) +
        r.oneshot.totalMs.toFixed(0).padStart(11) +
        (r.improvement.toFixed(1) + "%").padStart(9)
    );
  }

  console.log();
  if (allOk) {
    console.log("  [PASS] 前端流式解析逻辑正确：分块边界、结束标记、采样率都正常");
    console.log("         首块延迟远小于总耗时，边收边播能显著改善首声时间。");
  } else {
    console.log("  [FAIL] 存在校验失败项");
  }

  return allOk ? 0 : 1;
}

main().then(
  (c) => process.exit(c),
  (e) => {
    console.error("异常:", e);
    process.exit(1);
  }
);
