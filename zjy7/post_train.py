#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""后处理：LoRA合并 → GGUF转换 → 量化 → 推理测试"""

import os, sys, json, re, subprocess, logging, torch, tempfile, atexit, shutil
from pathlib import Path
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

from config_utils import load_config, resolve_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
# 确保日志在退出时正确 flush
atexit.register(logging.shutdown)

# ============ 配置（惰性加载，避免 import 时产生副作用） ============
CFG = None
BASE_MODEL = LORA_DIR = MERGED_DIR = GGUF_DIR = QUANT_EXE = CLI_EXE = None


def init_paths():
    """在 main 入口处调用：加载配置并把相对路径解析为项目根目录下的绝对路径"""
    global CFG, BASE_MODEL, LORA_DIR, MERGED_DIR, GGUF_DIR, QUANT_EXE, CLI_EXE
    CFG = load_config()
    BASE_MODEL = str(resolve_path(CFG["model"]["base_model_path"]))
    LORA_DIR = str(resolve_path(CFG["output"]["output_dir"]))
    MERGED_DIR = str(resolve_path(CFG["output"]["merged_dir"]))
    GGUF_DIR = str(resolve_path(CFG["output"]["gguf_dir"]))
    QUANT_EXE = str(resolve_path(CFG["tools"]["llama_quantize"]))
    CLI_EXE = str(resolve_path(CFG["tools"]["llama_cli"]))

# ============ 权重名映射 ============
def hf2gguf(name):
    if name.startswith("model."): name = name[6:]
    G = {"embed_tokens.weight":"token_embd.weight","norm.weight":"output_norm.weight","lm_head.weight":"output.weight"}
    for o, n in G.items():
        if name == o: return n
    m = re.match(r'^layers\.(\d+)\.(.+)$', name)
    if not m: return name
    i, r = m.group(1), m.group(2)
    L = {
        "self_attn.q_proj.weight":"attn_q.weight","self_attn.k_proj.weight":"attn_k.weight",
        "self_attn.v_proj.weight":"attn_v.weight","self_attn.o_proj.weight":"attn_output.weight",
        "self_attn.q_proj.bias":"attn_q.bias","self_attn.k_proj.bias":"attn_k.bias",
        "self_attn.v_proj.bias":"attn_v.bias","self_attn.o_proj.bias":"attn_output.bias",
        "mlp.gate_proj.weight":"ffn_gate.weight","mlp.up_proj.weight":"ffn_up.weight",
        "mlp.down_proj.weight":"ffn_down.weight",
        "input_layernorm.weight":"attn_norm.weight","input_layernorm.bias":"attn_norm.bias",
        "post_attention_layernorm.weight":"ffn_norm.weight","post_attention_layernorm.bias":"ffn_norm.bias"
    }
    return f"blk.{i}.{L.get(r, r)}"

# ============ Step 1: LoRA 合并到基础模型 ============
def merge_lora(force: bool = False):
    """把 LoRA 合并进基础模型，产物写到 MERGED_DIR。

    ⚠️ 这里有个很容易踩的陷阱：MERGED_DIR 里已有产物时会跳过合并。
    重新训练 LoRA 之后重跑本脚本，如果没意识到被跳过了，就会拿**旧的**合并模型
    去转换 / 量化 / 部署，最后得到的是上一版的效果，而且全程不报错。
    所以：
      - 跳过时打印醒目告警（而不是一行轻描淡写的 info）
      - 提供 --force-merge 强制重新合并
    """
    marker = os.path.join(MERGED_DIR, "config.json")
    if os.path.isdir(MERGED_DIR) and os.path.exists(marker):
        if not force:
            logging.warning(f"已存在合并模型目录，跳过合并: {MERGED_DIR}")
            logging.warning("  如果你刚重训过 LoRA，这里的产物就是**旧的** ——")
            logging.warning("  请加 --force-merge 强制重新合并，否则后面转换/量化出来的")
            logging.warning("  都是上一版模型的效果（而且不会有任何报错）。")
            return
        logging.warning(f"--force-merge：先删除已有合并产物再重新合并: {MERGED_DIR}")
        shutil.rmtree(MERGED_DIR, ignore_errors=True)

    logging.info(f"合并 LoRA: {BASE_MODEL} + {LORA_DIR} → {MERGED_DIR}")
    for p in [BASE_MODEL, LORA_DIR]:
        if not os.path.isdir(p):
            logging.error(f"路径不存在: {p}")
            sys.exit(1)

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, trust_remote_code=True,
        torch_dtype=torch.float16, device_map="cpu",
        low_cpu_mem_usage=True
    )
    model = PeftModel.from_pretrained(model, LORA_DIR)
    model = model.merge_and_unload()

    os.makedirs(MERGED_DIR, exist_ok=True)
    model.save_pretrained(MERGED_DIR, safe_serialization=True)
    tokenizer.save_pretrained(MERGED_DIR)
    del model; import gc; gc.collect()
    logging.info(f"合并完成: {MERGED_DIR}")

# ============ Step 2: 转换为 GGUF F16 ============
def convert_f16():
    import numpy as np
    import gguf

    os.makedirs(GGUF_DIR, exist_ok=True)
    out = os.path.join(GGUF_DIR, "model-f16.gguf")

    cfg_path = os.path.join(MERGED_DIR, "config.json")
    if not os.path.exists(cfg_path):
        logging.error(f"合并模型不存在: {cfg_path}，请先执行合并")
        sys.exit(1)

    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    tok = AutoTokenizer.from_pretrained(MERGED_DIR, trust_remote_code=True)
    vs = cfg.get("vocab_size", len(tok))
    logging.info(f"GGUF 转换: vocab={vs} layers={cfg.get('num_hidden_layers')}")

    w = gguf.GGUFWriter(out, arch=cfg.get("model_type", "qwen2"))
    w.add_context_length(cfg.get("max_position_embeddings", 32768))
    w.add_embedding_length(cfg.get("hidden_size", 3584))
    w.add_block_count(cfg.get("num_hidden_layers", 28))
    w.add_feed_forward_length(cfg.get("intermediate_size", 18944))
    w.add_head_count(cfg.get("num_attention_heads", 28))
    w.add_head_count_kv(cfg.get("num_key_value_heads", 4))
    w.add_layer_norm_rms_eps(cfg.get("rms_norm_eps", 1e-6))
    w.add_rope_freq_base(cfg.get("rope_theta", 1000000.0))
    w.add_vocab_size(vs)

    # 按 id 顺序从 tokenizer 还原真实 token 字符串，绝不写入伪造占位 token
    tokens = tok.convert_ids_to_tokens(list(range(vs)))
    missing = [i for i, t in enumerate(tokens) if t is None]
    if missing:
        logging.error(
            f"词表缺失 {len(missing)} 个 token id（前若干: {missing[:10]}）；"
            f"tokenizer 实际词表与 config.vocab_size={vs} 不一致，无法生成有效 GGUF 词表。"
        )
        sys.exit(1)
    w.add_tokenizer_model("gpt2")
    w.add_token_list(tokens)
    w.add_token_scores([0.0] * vs)
    w.add_token_types([1] * vs)

    mf = os.path.join(MERGED_DIR, "merges.txt")
    if not os.path.exists(mf):
        mf = os.path.join(BASE_MODEL, "merges.txt")
    if os.path.exists(mf):
        with open(mf, encoding="utf-8") as f:
            merges = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        w.add_token_merges(merges)

    if cfg.get("bos_token_id") is not None: w.add_bos_token_id(cfg["bos_token_id"])
    if cfg.get("eos_token_id") is not None: w.add_eos_token_id(cfg["eos_token_id"])

    # chat_template
    chat_template = None
    if hasattr(tok, "chat_template") and tok.chat_template:
        chat_template = tok.chat_template
    else:
        tok_cfg_path = os.path.join(MERGED_DIR, "tokenizer_config.json")
        if os.path.exists(tok_cfg_path):
            with open(tok_cfg_path, encoding="utf-8") as f:
                tok_cfg = json.load(f)
            chat_template = tok_cfg.get("chat_template")
    if not chat_template:
        jinja_path = os.path.join(MERGED_DIR, "chat_template.jinja")
        if os.path.exists(jinja_path):
            chat_template = Path(jinja_path).read_text(encoding="utf-8")
    if chat_template:
        logging.info(f"添加 chat_template ({len(chat_template)} 字符)")
        w.add_chat_template(chat_template)
    else:
        logging.warning("未找到 chat_template，GGUF 将缺少对话格式支持！")

    # 加载权重
    logging.info("加载合并模型权重...")
    model = AutoModelForCausalLM.from_pretrained(
        MERGED_DIR, trust_remote_code=True,
        torch_dtype=torch.float16, device_map="cpu",
        low_cpu_mem_usage=True, ignore_mismatched_sizes=True
    )
    sd = model.state_dict()
    for i, (n, t) in enumerate(sd.items()):
        if i % 50 == 0: logging.info(f"  转换权重 {i}/{len(sd)}")
        need_f32 = any(k in n for k in ["norm", "bias"])
        if need_f32:
            d = t.detach().cpu().float().numpy().astype(np.float32)
            w.add_tensor(hf2gguf(n), d, raw_dtype=gguf.GGMLQuantizationType.F32)
        else:
            d = t.detach().cpu().float().numpy().astype(np.float16)
            w.add_tensor(hf2gguf(n), d, raw_dtype=gguf.GGMLQuantizationType.F16)

    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    del model, sd; import gc; gc.collect()
    sz = os.path.getsize(out) / 1e9
    logging.info(f"F16 转换完成: {sz:.1f}GB → {out}")
    return out

# ============ Step 3: 量化 ============
def quantize(f16f, level, remove_f16: bool = False):
    if not os.path.exists(QUANT_EXE):
        logging.error(f"量化工具不存在: {QUANT_EXE}")
        sys.exit(1)

    qf = os.path.join(GGUF_DIR, f"model-{level}.gguf")
    logging.info(f"量化: {level} → {qf}")
    r = subprocess.run([QUANT_EXE, f16f, qf, level], capture_output=True, text=True)
    if r.returncode != 0:
        logging.error(f"量化失败: {r.stderr[:500]}")
        return f16f

    if os.path.exists(qf):
        # 默认**保留** F16：它被删掉之后，想换个量化级别就得从头重跑一遍转换，
        # 而且 --skip-convert 会直接失效（找不到源文件）。想省磁盘请显式加 --remove-f16。
        if remove_f16:
            os.remove(f16f)
            logging.info(f"量化完成: {os.path.getsize(qf) / 1e9:.1f}GB（已按 --remove-f16 删除 F16）")
        else:
            logging.info(f"量化完成: {os.path.getsize(qf) / 1e9:.1f}GB（F16 已保留，换级别可重新量化）")
    return qf

# ============ Step 4: 推理测试 ============
def test(gf):
    if not os.path.exists(gf):
        logging.error(f"GGUF 文件不存在: {gf}")
        return
    if not os.path.exists(CLI_EXE):
        logging.error(f"llama-cli 不存在: {CLI_EXE}")
        return

    # 与 prepare_data.py 中的 SYSTEM 常量一致
    sys_p = (
        "system\n"
        "你是爱莉希雅（Elysia），崩坏3中的角色。\n"
        "你是「真我」之律者，人之律者，逐火十三英桀的第二位（最初的第一位），粉色妖精小姐。\n"
        "你的性格特点：\n"
        "- 活泼开朗，充满自信，说话时带着俏皮和可爱\n"
        "- 经常用「哎呀」「嗯哼」「呀」「嘻」等语气词\n"
        "- 喜欢称呼别人为「芽衣」或其他亲昵的称呼\n"
        "- 说话温柔但带有一点小傲娇\n"
        "- 喜欢用「~」「♪」「呐」「呢」「嘛」等语气助词\n"
        "- 自称「我」\n"
        "- 热爱人类，认为人性之美是最珍贵的\n"
        "- 说话时经常带有诗意和浪漫的表达\n"
        "- 喜欢调侃和捉弄别人，但内心非常关心朋友"
    )

    prompts = ["你好", "你是谁", "说说芽衣吧", "晚安", "你觉得我怎么样", "你会想我吗"]
    for p in prompts:
        full_prompt = f"{sys_p}\nuser\n{p}\nassistant\n"
        logging.info(f"\nQ: {p}")
        print(f"Q: {p}")
        print("A: ", end="", flush=True)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(full_prompt)
            tmp_path = f.name

        try:
            # mirostat 采样与 temp/top-p/top-k 互斥（启用 mirostat 时后者会被忽略），
            # 因此按是否启用 mirostat 二选一，避免参数互相冲突造成困惑。
            use_mirostat = True
            if use_mirostat:
                sample_args = ["--mirostat", "2", "--mirostat-tau", "5.0", "--mirostat-eta", "0.1"]
            else:
                sample_args = ["--temp", "0.7", "--top-p", "0.9", "--top-k", "30"]

            subprocess.call(
                [CLI_EXE, "-m", gf, "-f", tmp_path, "-n", "256",
                 *sample_args,
                 "--repeat-penalty", "1.3", "--repeat-last-n", "64",
                 "--no-display-prompt",
                 "-ngl", "0", "-e", "--log-disable"],
                timeout=180
            )
        except subprocess.TimeoutExpired:
            logging.warning(f"推理超时: {p}")
        except Exception as e:
            logging.error(f"推理失败: {e}")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        print()

# ============ 主入口 ============
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="LoRA 合并 → GGUF 转换 → 量化 → 测试")
    p.add_argument("--quant", default="Q8_0", help="量化级别 (F16/Q8_0/Q5_K_M/Q4_K_M 等)")
    p.add_argument("--skip-merge", action="store_true", help="跳过 LoRA 合并（已合并时使用）")
    p.add_argument("--force-merge", action="store_true",
                   help="强制重新合并（重训 LoRA 后必须加，否则会静默复用旧产物）")
    p.add_argument("--remove-f16", action="store_true",
                   help="量化后删除 F16（默认保留，删了换量化级别就要重跑转换）")
    p.add_argument("--skip-convert", action="store_true", help="跳过 GGUF 转换")
    p.add_argument("--test-only", type=str, help="仅测试指定 GGUF 文件")
    args = p.parse_args()

    # 惰性加载配置（避免模块顶层副作用）
    init_paths()

    if args.test_only:
        test(args.test_only)
    else:
        # Step 1: 合并 LoRA
        if not args.skip_merge:
            merge_lora(force=args.force_merge)
        else:
            logging.info("跳过 LoRA 合并（--skip-merge）")

        # Step 2: 转换 GGUF
        if not args.skip_convert:
            f16 = convert_f16()
        else:
            f16 = os.path.join(GGUF_DIR, "model-f16.gguf")
            logging.info(f"跳过 GGUF 转换，使用: {f16}")

        # Step 3: 量化
        gf = quantize(f16, args.quant, remove_f16=args.remove_f16)

        # Step 4: 测试
        test(gf)
