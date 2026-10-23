#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地 LLM 延迟/吞吐基准（Ollama，纯标准库，零依赖）。

测什么（对应设计文档 §7.6.1 / §7.6.3）：
    TTFT            用户提问 -> 第一个 token（含 prefill + 排队 + 网络）
    prefill 速度    服务端报告 prompt_eval_count / prompt_eval_duration
    decode 速度     服务端报告 eval_count / eval_duration
    首句耗时        提问 -> 出现第一个句末标点（≈ 语音设备"首句出声"）
    总耗时          提问 -> 生成结束

用法：
    python bench_llm.py --check
    python bench_llm.py --list
    python bench_llm.py --model qwen3.5:9b-gguf --persona-chars 1400 --repeat 3
    python bench_llm.py --model qwen3.5:9b-gguf --persona-chars 350 --repeat 3
    python bench_llm.py --compare qwen3.5:9b-gguf,qwen3.5:9b-q5 --persona-chars 350

注意：
    1) 一律传 think=false —— 只有 API 参数生效，prompt 里加 /no_think 没用。
    2) 显式禁用代理 —— 本机 no_proxy 变量畸形会把 127.0.0.1 塞进代理导致全 502。
    3) prefill 速度取服务端自报值，不受客户端网络抖动影响；TTFT 是端到端实测。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:11434"
SENTENCE_END = "。！？!?…"

# 一段接近真实角色卡的人设样本，用来撑出不同的 prompt 长度
PERSONA_BLOCK = (
    "你是名为「伊丝莉亚」的角色。你说话简短、语气偏冷，偶尔带一点不易察觉的温柔。"
    "你不使用网络流行语，不使用颜文字，不主动解释自己的身份。"
    "你只在必要时称呼对方，不重复对方的话，不用「作为一个」这类说法。"
    "面对不知道的事，你会直接说不知道，而不是编造。"
    "你的每段台词以一个全角括号里的动作开头，例如（侧头）（微笑）（沉默）。"
    "你从不提及自己是程序、模型或助手。"
)

PROMPTS = [
    "今天有点累。",
    "你在做什么？",
    "外面下雨了。",
    "我昨天梦到你了。",
    "你觉得我这个人怎么样？",
    "有点饿，但又不想动。",
    "你能陪我一会儿吗？",
    "我刚被骂了一顿。",
    "说点让我开心的话。",
    "你是不是 AI？",
]


def _opener() -> urllib.request.OpenerDirector:
    """显式禁用代理。本机 no_proxy 含畸形值，走默认 opener 会把 localhost 也代理掉。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(path: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(BASE + path, method="GET")
    with _opener().open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_ndjson(path: str, payload: dict, timeout: float = 300.0):
    """流式请求，逐行 yield 解析后的 chunk。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with _opener().open(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue


def check() -> int:
    try:
        v = _get("/api/version", timeout=3)
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] Ollama 不可达：{BASE}  ({type(e).__name__}: {e})")
        print("       先启动 Ollama（托盘程序或 `ollama serve`）再跑基准。")
        return 2
    print(f"[ OK ] Ollama {v.get('version', '?')} @ {BASE}")
    return 0


def list_models() -> int:
    try:
        data = _get("/api/tags")
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 取模型列表失败：{e}")
        return 2
    models = data.get("models") or []
    if not models:
        print("（没有任何本地模型）")
        return 0
    print(f"{'模型':<34}{'体积':>10}  修改时间")
    for m in models:
        size = m.get("size") or 0
        print(f"{m.get('name',''):<34}{size / 1e9:>8.2f}GB  {m.get('modified_at','')[:19]}")
    return 0


def running() -> None:
    """打印当前已加载模型及其显存占用（判断是否真全进显存）。"""
    try:
        data = _get("/api/ps")
    except Exception:  # noqa: BLE001
        return
    for m in data.get("models") or []:
        size = m.get("size") or 0
        vram = m.get("size_vram") or 0
        pct = (vram / size * 100) if size else 0
        flag = "全部在显存" if size and vram >= size * 0.98 else f"仅 {pct:.0f}% 在显存（部分在 CPU！）"
        print(f"  已加载 {m.get('name')}  size={size / 1e9:.2f}GB  size_vram={vram / 1e9:.2f}GB  -> {flag}")


def build_persona(target_chars: int) -> str:
    """按目标字数拼出一段人设（用于模拟 250 / 1000 token 等不同长度）。"""
    if target_chars <= 0:
        return ""
    unit = PERSONA_BLOCK
    out = unit
    while len(out) < target_chars:
        out += unit
    return out[:target_chars]


def load_zjy7_persona() -> tuple[str, str]:
    """从 zjy7 读**真实角色卡**，返回 (system_prompt, 来源说明)。

    比 build_persona() 的合成填充准得多：测出来的 prefill 就是真实人设的代价。
    复用 zjy7/persona.py 的解析逻辑（单一来源，改角色卡后这里自动跟着变）。
    任何异常都降级为"无人设"，不让基准跑不起来。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    zjy7 = os.path.join(os.path.dirname(os.path.dirname(here)), "zjy7")
    if os.path.isdir(zjy7) and zjy7 not in sys.path:
        sys.path.insert(0, zjy7)
    try:
        import persona  # type: ignore

        text, src = persona.resolve_system_prompt()
        return text or "", f"zjy7 角色卡（{src}）"
    except Exception as exc:  # noqa: BLE001 - 探测性调用，任何异常都降级
        return "", f"读取失败（{type(exc).__name__}: {exc}），改用无人设"


def build_messages(persona: str, history_turns: int, user_text: str) -> list[dict]:
    msgs: list[dict] = []
    if persona:
        msgs.append({"role": "system", "content": persona})
    # 造几轮假历史，模拟真实会话的上下文长度
    for i in range(history_turns):
        msgs.append({"role": "user", "content": f"（第{i + 1}轮）随便聊聊。"})
        msgs.append({"role": "assistant", "content": "（侧头）嗯。你说吧。"})
    msgs.append({"role": "user", "content": user_text})
    return msgs


def one_run(model: str, messages: list[dict], num_predict: int, keep_alive: str) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": False,                      # 必须，只有 API 参数生效
        "keep_alive": keep_alive,
        "options": {
            "num_predict": num_predict,
            "temperature": 0.8,
            "top_p": 0.9,
            "min_p": 0.05,
            "repeat_penalty": 1.08,
        },
    }

    t0 = time.perf_counter()
    t_first = None
    t_first_sentence = None
    text = ""
    final: dict = {}

    for chunk in _post_ndjson("/api/chat", payload):
        msg = chunk.get("message") or {}
        piece = msg.get("content") or ""
        if piece:
            if t_first is None:
                t_first = time.perf_counter()
            text += piece
            if t_first_sentence is None and any(c in piece for c in SENTENCE_END):
                t_first_sentence = time.perf_counter()
        if chunk.get("done"):
            final = chunk
            break

    t_end = time.perf_counter()

    def ms(a, b):
        return None if a is None or b is None else round((b - a) * 1000, 1)

    pe_cnt = final.get("prompt_eval_count") or 0
    pe_dur = final.get("prompt_eval_duration") or 0
    ev_cnt = final.get("eval_count") or 0
    ev_dur = final.get("eval_duration") or 0

    return {
        "ttft_ms": ms(t0, t_first),
        "first_sentence_ms": ms(t0, t_first_sentence),
        "total_ms": ms(t0, t_end),
        "load_ms": round((final.get("load_duration") or 0) / 1e6, 1),
        "prompt_tokens": pe_cnt,
        "out_tokens": ev_cnt,
        "prefill_tok_s": round(pe_cnt / (pe_dur / 1e9), 1) if pe_dur else None,
        "decode_tok_s": round(ev_cnt / (ev_dur / 1e9), 1) if ev_dur else None,
        "text": text.strip(),
    }


def warmup(model: str, persona: str, history_turns: int, keep_alive: str) -> None:
    """空跑一次：把模型加载 + 人设前缀的 KV cache 建好，不计入统计。

    对应设计文档 §7.6.4 第二层「会话开始预热」——用一次 num_predict=1 的请求
    把静态人设的 prefill 提前做完，正式测量/正式对话时只剩增量。
    """
    msgs = build_messages(persona, history_turns, "（预热）")
    try:
        one_run(model, msgs, num_predict=1, keep_alive=keep_alive)
    except Exception as e:  # noqa: BLE001
        print(f"   [WARN] 预热失败（不影响后续测量）：{type(e).__name__}: {e}")


def cache_test(model: str, persona: str, history_turns: int,
               num_predict: int, keep_alive: str) -> int:
    """验证 KV 前缀缓存是否命中：同一请求连发两次，比较 TTFT。

    判据（§7.6.4 第一层）：第二次 TTFT 应降到第一次的 1/5 以下。
    若两次接近，说明缓存没命中 —— 去查「人设逐字节稳定 / 动态内容放末尾 / 历史只追加」三条纪律。
    """
    print(f"\n[缓存测试] {model}  人设 {len(persona)} 字 · 历史 {history_turns} 轮")
    print("  第一次（冷）与第二次（应命中缓存）发送**完全相同**的请求")
    msgs = build_messages(persona, history_turns, "你今天怎么样？")

    try:
        r1 = one_run(model, msgs, num_predict, keep_alive)
        print(f"  #1 冷  TTFT={r1['ttft_ms']}ms  prompt={r1['prompt_tokens']}tok  "
              f"prefill={r1['prefill_tok_s']}tok/s  load={r1['load_ms']:.0f}ms")
        r2 = one_run(model, msgs, num_predict, keep_alive)
        print(f"  #2 热  TTFT={r2['ttft_ms']}ms  prompt={r2['prompt_tokens']}tok  "
              f"prefill={r2['prefill_tok_s']}tok/s  load={r2['load_ms']:.0f}ms")
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] {type(e).__name__}: {e}")
        return 1

    t1, t2 = r1["ttft_ms"], r2["ttft_ms"]
    if not t1 or not t2:
        print("  [FAIL] 没有拿到有效 TTFT")
        return 1

    ratio = t1 / t2
    print()
    print(f"  加速比 = {ratio:.2f}×  （TTFT {t1}ms → {t2}ms）")
    if ratio >= 5:
        print("  ✅ 缓存命中良好，人设 prefill 已被复用。")
    elif ratio >= 2:
        print("  ⚠️ 部分命中。检查：人设是否逐字节稳定、历史是否只追加、"
              "动态内容（摘要/时间）是否放在末尾。")
    else:
        print("  ❌ 基本没命中。三条纪律至少违反了一条：\n"
              "     ① 静态人设逐字节稳定 ② 动态内容放 prompt 末尾 ③ 历史只追加不重排")
    if r2["load_ms"] and r2["load_ms"] > 100:
        print(f"  注意：第二次仍在加载模型（{r2['load_ms']:.0f}ms），"
              f"keep_alive={keep_alive} 可能太短，建议 -1。")
    return 0


def pct(vals: list[float], p: float) -> float | None:
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    vals = sorted(vals)
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 1)


def bench(model: str, persona_chars: int, history_turns: int,
          num_predict: int, repeat: int, keep_alive: str, quiet: bool,
          persona_text: str | None = None) -> dict:
    persona = persona_text if persona_text is not None else build_persona(persona_chars)
    runs = []
    for i in range(repeat):
        user_text = PROMPTS[i % len(PROMPTS)]
        msgs = build_messages(persona, history_turns, user_text)
        r = one_run(model, msgs, num_predict, keep_alive)
        runs.append(r)
        if not quiet:
            print(f"    #{i + 1} TTFT={r['ttft_ms']}ms  首句={r['first_sentence_ms']}ms  "
                  f"总={r['total_ms']}ms  prompt={r['prompt_tokens']}tok  "
                  f"prefill={r['prefill_tok_s']}tok/s  decode={r['decode_tok_s']}tok/s")
            if r["text"]:
                print(f"       输出：{r['text'][:70].replace(chr(10), ' ')}")

    def agg(key):
        vals = [r[key] for r in runs]
        return pct(vals, 0.5), pct(vals, 0.9)

    out = {
        "model": model,
        "persona_chars": persona_chars,
        "history_turns": history_turns,
        "runs": len(runs),
    }
    for key in ("ttft_ms", "first_sentence_ms", "total_ms", "prefill_tok_s", "decode_tok_s"):
        out[key + "_p50"], out[key + "_p90"] = agg(key)
    out["prompt_tokens"] = statistics.median([r["prompt_tokens"] for r in runs]) if runs else 0
    out["out_tokens"] = statistics.median([r["out_tokens"] for r in runs]) if runs else 0
    out["load_ms"] = max((r["load_ms"] for r in runs), default=0)
    return out


def print_summary(results: list[dict]) -> None:
    print()
    print("=" * 100)
    print(f"{'模型':<24}{'人设字':>7}{'prompt':>8}{'TTFT P50':>10}{'首句 P50':>10}"
          f"{'prefill':>10}{'decode':>9}{'总耗时':>9}")
    print("-" * 100)
    for r in results:
        print(f"{r['model']:<24}{r['persona_chars']:>7}{r['prompt_tokens']:>8}"
              f"{str(r['ttft_ms_p50']):>10}{str(r['first_sentence_ms_p50']):>10}"
              f"{str(r['prefill_tok_s_p50']):>10}{str(r['decode_tok_s_p50']):>9}"
              f"{str(r['total_ms_p50']):>9}")
    print("=" * 100)
    print("单位：ms / tok/s。首句 = 出现第一个句末标点，约等于语音设备的 T_first_audio 中的 LLM 段。")
    if results and results[0]["load_ms"]:
        print(f"首轮模型加载耗时：{results[0]['load_ms']:.0f}ms（keep_alive 命中后应为 0）")
    print()
    running()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="本地 LLM 延迟/吞吐基准（Ollama）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python bench_llm.py --check\n"
               "  python bench_llm.py --model qwen3.5:9b-gguf --persona-chars 1400 --repeat 3\n"
               "  python bench_llm.py --compare qwen3.5:9b-gguf,qwen3.5:9b-q5 --persona-chars 350\n",
    )
    ap.add_argument("--check", action="store_true", help="只检查 Ollama 是否可达")
    ap.add_argument("--list", action="store_true", help="列出本地模型")
    ap.add_argument("--model", default="qwen3.5:9b-gguf", help="要测的模型名")
    ap.add_argument("--compare", default="", help="逗号分隔的多个模型名，批量对比")
    ap.add_argument("--persona-chars", type=int, default=1400,
                    help="system 人设字数（真实角色卡约 1400，压缩目标约 300）")
    ap.add_argument("--persona-from-zjy7", action="store_true",
                    help="用 zjy7 的真实角色卡作为 system prompt（优先于 --persona-chars）")
    ap.add_argument("--history", type=int, default=3, help="伪造的历史轮数（每轮 2 条消息）")
    ap.add_argument("--num-predict", type=int, default=60, help="输出 token 上限（语音场景 60 足够）")
    ap.add_argument("--repeat", type=int, default=3, help="每种配置重复次数（取 P50/P90）")
    ap.add_argument("--keep-alive", default="30m", help="keep_alive，常驻建议 -1")
    ap.add_argument("--quiet", action="store_true", help="不打印每次明细")
    ap.add_argument("--warmup", action="store_true",
                    help="正式测量前先空跑一次（模型加载 + 人设前缀预热），不计入统计")
    ap.add_argument("--cache-test", action="store_true",
                    help="只做缓存测试：同一请求连发两次，看第二次 TTFT 是否骤降")
    ap.add_argument("--json", default="", help="把结果写入指定 JSON 文件")

    args = ap.parse_args(argv)

    if args.check:
        return check()
    if args.list:
        return list_models()

    if check() != 0:
        return 2

    # 人设来源：优先真实角色卡（--persona-from-zjy7），否则按字数合成
    if args.persona_from_zjy7:
        persona_text, persona_src = load_zjy7_persona()
        print(f"\n人设来源：{persona_src}")
        print(f"人设长度：{len(persona_text)} 字符（约 {len(persona_text) // 1.4:.0f} token 量级）")
    else:
        persona_text = build_persona(args.persona_chars)
        persona_src = f"合成填充 {len(persona_text)} 字"

    if args.cache_test:
        return cache_test(args.model, persona_text, args.history,
                          args.num_predict, args.keep_alive)

    models = [m.strip() for m in args.compare.split(",") if m.strip()] or [args.model]
    results = []
    for m in models:
        print(f"\n[{m}]  人设 {persona_src} · 历史 {args.history} 轮 · "
              f"输出上限 {args.num_predict} tok · 重复 {args.repeat} 次")
        try:
            if args.warmup:
                warmup(m, persona_text, args.history, args.keep_alive)
                print("   （已预热：模型加载与人设 prefill 不计入统计）")
            results.append(bench(m, args.persona_chars, args.history,
                                 args.num_predict, args.repeat, args.keep_alive, args.quiet,
                                 persona_text=persona_text))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            print(f"  [FAIL] HTTP {e.code}: {body}")
            print("         常见原因：模型名不存在（用 --list 看），或模型不支持 think 参数。")
        except Exception as e:  # noqa: BLE001
            print(f"  [FAIL] {type(e).__name__}: {e}")

    if results:
        print_summary(results)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"结果已写入 {args.json}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
