#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zjy7 流水线编排：一条命令跑完全流程，失败即停。

以前要手动串 5 个脚本，顺序得自己记：

    python prepare_data.py
    python train_lora.py
    python post_train.py --quant Q8_0
    python evaluate.py
    python deploy_ollama.py

问题有两个：
  1. 任何一步失败都得自己发现 —— 不看终端就不知道后面几步其实是在跑旧产物
  2. **最容易踩的**：重训 LoRA 之后跑 post_train 必须加 --force-merge，
     否则它会静默复用旧的合并模型，最后部署出去的是上一版的效果

本脚本把这两点都处理掉：默认失败即停；只要本次真的跑了训练，
就自动给 post_train 带上 --force-merge。

用法：

    python run_all.py                       # 全流程（默认跳过 3 个可选步骤）
    python run_all.py --dry-run             # 只打印将要执行什么，不实际跑
    python run_all.py --with-collect        # 连 zjy5 → zjy7 的语料收集也跑
    python run_all.py --with-questions      # 连「生成提问」也跑（需本地 Ollama）
    python run_all.py --with-general        # 连「生成通用数据」也跑（需本地 Ollama）
    python run_all.py --from train          # 从 train 开始（跳过数据准备）
    python run_all.py --only train,post     # 只跑指定步骤
    python run_all.py --quant Q5_K_M        # 指定量化级别
    python run_all.py --keep-going          # 失败也继续（默认失败即停）

关于两个可选步骤（默认跳过，见 OPTIONAL_STEPS）：

    collect   把 zjy5 抽出的台词并进 downloads/。依赖 zjy5 已经产出过台词文件，
              zjy7 单独使用时通常没有。
    questions 用本地 LLM 给每条台词生成 3 个不同问法（治提问坍缩）。
              需要 Ollama 在跑 —— 没有也能跑，但会退回模板问句、效果差很多。
    general   生成通用指令数据（防灾难性遗忘）。同样需要 Ollama；
              没有只会告警、不混通用数据，模型有「问啥都往角色上拐」的风险。

    硬跑它们会立刻失败或静默降级、挡住整条流水线，所以要显式开启。
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


class Step:
    def __init__(self, key, name, script, desc):
        self.key = key
        self.name = name
        self.script = script
        self.desc = desc


# 步骤顺序即执行顺序。key 用于 --from / --only。
STEPS = [
    Step("collect", "收集语料",      "collect_corpus.py", "把 zjy5 抽出的台词并进 downloads/（zjy5 → zjy7 直通）"),
    Step("questions", "生成提问",    "gen_questions.py",  "用本地 LLM 给每条台词生成 3 个不同问法（治提问坍缩）"),
    Step("general", "生成通用数据",  "gen_general.py",    "生成通用指令数据（防灾难性遗忘）"),
    Step("data",   "准备训练数据",   "prepare_data.py",   "由内置语料 + downloads/*.txt 生成 train_data.json"),
    Step("stats",  "数据统计",       "data_stats.py",     "检查数据分布（数量、长度、重复）"),
    Step("train",  "LoRA 训练",      "train_lora.py",     "4-bit NF4 + LoRA 微调，支持 --resume 断点续训"),
    Step("post",   "合并/转换/量化", "post_train.py",     "LoRA 合并 → GGUF F16 → 量化 → 推理自测"),
    Step("eval",   "独立评估",       "evaluate.py",       "用独立评估集算 ppl / 角色一致性 / 脱角色检测"),
    Step("deploy", "生成 Modelfile", "deploy_ollama.py",  "生成 Ollama 的 Modelfile（会覆盖同名文件）"),
]

# 默认不跑的步骤：
# - collect 依赖 zjy5 已经跑出台词文件，zjy7 单独使用时多半找不到源文件；
# - questions 需要本地 Ollama 在跑（没有也能跑，但会退回模板问句、效果差很多）。
# 硬跑它们会立刻失败或静默降级，挡住整条流水线 —— 所以默认跳过，
# 要跑全链路时显式加 --with-collect / --with-questions。
OPTIONAL_STEPS = {"collect", "questions", "general"}

KEYS = [s.key for s in STEPS]


def build_cmd(step, args, trained_this_run):
    """把每个步骤翻译成实际的命令行。

    这里最值得留意的是 post 步骤的 --force-merge 判断，见文件头说明。
    """
    if step.key == "train":
        cmd = [sys.executable, step.script]
        if args.resume:
            cmd += ["--resume", args.resume]
        return cmd

    if step.key == "post":
        cmd = [sys.executable, step.script, "--quant", args.quant]
        # 本次真的训练过 → LoRA 已经变了 → 必须强制重新合并，
        # 否则 post_train 会静默复用旧的合并产物。
        if trained_this_run:
            cmd.append("--force-merge")
        return cmd

    if step.key == "eval":
        return [sys.executable, step.script, "--mode", "all"]

    if step.key == "deploy":
        return [sys.executable, step.script, "--num-ctx", str(args.num_ctx)]

    return [sys.executable, step.script]


def fmt_secs(sec):
    if sec < 60:
        return f"{sec:.1f}s"
    m, s = divmod(int(sec), 60)
    return f"{m}m{s:02d}s"


def main():
    p = argparse.ArgumentParser(
        description="zjy7 LoRA 流水线编排（失败即停）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="步骤 key：" + " / ".join(KEYS),
    )
    p.add_argument("--dry-run", action="store_true", help="只打印将要执行什么，不实际运行")
    p.add_argument("--from", dest="from_step", choices=KEYS, metavar="STEP",
                   help=f"从某一步开始（{'/'.join(KEYS)}）")
    p.add_argument("--only", metavar="STEP[,STEP]",
                   help="只跑指定步骤，逗号分隔（如 train,post）")
    p.add_argument("--quant", default="Q8_0", help="量化级别（默认 Q8_0）")
    p.add_argument("--resume", nargs="?", const="latest", metavar="CKPT",
                   help="训练时断点续训（不带值 = latest）")
    p.add_argument("--num-ctx", type=int, default=2048, help="生成的 Modelfile 的上下文长度")
    p.add_argument("--keep-going", action="store_true",
                   help="某步失败也继续往下跑（默认失败即停）")
    p.add_argument("--with-collect", action="store_true",
                   help="全流程时也跑「收集语料」步骤（需先有 zjy5 的台词文件）")
    p.add_argument("--with-questions", action="store_true",
                   help="全流程时也跑「生成提问」步骤（需本地 Ollama 在跑）")
    p.add_argument("--with-general", action="store_true",
                   help="全流程时也跑「生成通用数据」步骤（需本地 Ollama 在跑）")
    args = p.parse_args()

    # ---- 决定要跑哪些步骤 ----
    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        unknown = [k for k in wanted if k not in KEYS]
        if unknown:
            print(f"❌ 未知的步骤：{unknown}\n   可用：{KEYS}")
            return 2
        selected = [s for s in STEPS if s.key in wanted]
    elif args.from_step:
        idx = KEYS.index(args.from_step)
        selected = STEPS[idx:]
    else:
        selected = list(STEPS)
        # 全量跑时默认跳过可选步骤（见 OPTIONAL_STEPS 的说明）。
        # 两个开关各自只放行对应那一步，避免「开了 collect 结果 questions 也被跑」。
        allow = set()
        if args.with_collect:
            allow.add("collect")
        if args.with_questions:
            allow.add("questions")
        if args.with_general:
            allow.add("general")
        selected = [s for s in selected
                    if s.key not in OPTIONAL_STEPS or s.key in allow]

    # ---- 打印计划 ----
    print("=" * 64)
    print("zjy7 LoRA 流水线")
    print("=" * 64)
    print(f"项目目录：{PROJECT_DIR}")
    print(f"将执行 {len(selected)} 个步骤：" + " → ".join(s.key for s in selected))
    if args.dry_run:
        print("（--dry-run：只打印计划，不实际执行）")
    print()

    trained_this_run = False
    results = []

    for i, step in enumerate(selected, 1):
        cmd = build_cmd(step, args, trained_this_run)
        cmd_display = " ".join(
            ("python" if c == sys.executable else c) for c in cmd
        )

        print("-" * 64)
        print(f"[{i}/{len(selected)}] {step.name}  ({step.key})")
        print(f"    {step.desc}")
        print(f"    $ {cmd_display}")

        # dry-run 也把 post 步骤的 --force-merge 决策显示出来，
        # 好让人看到"本次训练过 → 会自动强制重合并"这条规则确实生效了。
        if args.dry_run and step.key == "post" and not trained_this_run:
            print("    注：若本次真的跑了 train，这里会自动追加 --force-merge")

        if args.dry_run:
            results.append((step.key, "SKIP", 0.0))
            continue

        # 检查脚本是否存在 —— 报错要具体到缺哪个文件，而不是一句"失败"
        script_path = PROJECT_DIR / step.script
        if not script_path.exists():
            print(f"❌ 找不到脚本：{script_path}")
            results.append((step.key, "MISSING", 0.0))
            if not args.keep_going:
                break
            continue

        started = time.time()
        try:
            # 不捕获输出：让子进程直接写到终端，进度条 / 训练日志都能实时看到
            proc = subprocess.run(cmd, cwd=str(PROJECT_DIR))
            rc = proc.returncode
        except KeyboardInterrupt:
            print("\n⏹  用户中断")
            results.append((step.key, "INTERRUPTED", time.time() - started))
            break
        except Exception as e:
            print(f"❌ 启动失败：{type(e).__name__}: {e}")
            results.append((step.key, "ERROR", time.time() - started))
            if not args.keep_going:
                break
            continue

        elapsed = time.time() - started
        if rc == 0:
            print(f"✅ {step.name} 完成（{fmt_secs(elapsed)}）")
            results.append((step.key, "OK", elapsed))
            if step.key == "train":
                # 记下来：后面 post 步骤要据此加 --force-merge
                trained_this_run = True
        else:
            print(f"❌ {step.name} 失败（退出码 {rc}，{fmt_secs(elapsed)}）")
            results.append((step.key, "FAILED", elapsed))
            if not args.keep_going:
                print("\n⛔ 失败即停。修好之后可以从这一步继续：")
                print(f"   python run_all.py --from {step.key}")
                break

    # ---- 汇总 ----
    print()
    print("=" * 64)
    print("汇总")
    print("=" * 64)
    for key, status, elapsed in results:
        icon = {"OK": "✅", "SKIP": "⏭ ", "FAILED": "❌",
                "MISSING": "❓", "ERROR": "❌", "INTERRUPTED": "⏹ "}.get(status, "?")
        name = next((s.name for s in STEPS if s.key == key), key)
        print(f"  {icon} {name:<14} {status:<12} {fmt_secs(elapsed) if elapsed else ''}")

    failed = [k for k, st, _ in results if st in ("FAILED", "ERROR", "MISSING")]
    skipped = [s.key for s in selected if s.key not in [k for k, _, _ in results]]

    print()
    if failed:
        print(f"结果：❌ 有 {len(failed)} 个步骤失败：{failed}")
        return 1
    if skipped and not args.dry_run:
        print(f"结果：⏹  未执行：{skipped}")
        return 1
    if args.dry_run:
        print("结果：✅ 计划已打印（未实际执行）")
    else:
        print("结果：✅ 全部步骤完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
