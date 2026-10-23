# bench · 本地 LLM 基准

对应设计文档 §7.6.1 / §7.6.3。纯标准库，零依赖，直接用系统 Python 跑。

## 前置

```bash
# Ollama 必须在跑（托盘程序，或手动 ollama serve）
python bench_llm.py --check     # 确认可达
python bench_llm.py --list      # 看模型名，别猜
```

## 先用这两个命令回答"prefill 优化有没有效"

对应设计文档 §7.6.4。不用 LoRA、纯角色卡时，prefill 优化的第一件事不是压人设，而是**让 prefill 只算一次**。

```bash
# ① 缓存测试：同一请求连发两次，第二次 TTFT 应降到 1/5 以下
python bench_llm.py --cache-test --model qwen3.5:9b-gguf --persona-from-zjy7

# ② 预热效果：正式测量前空跑一次（模拟"按键即预热"）
python bench_llm.py --model qwen3.5:9b-gguf --persona-from-zjy7 --warmup --repeat 3
```

**`--persona-from-zjy7` 建议常开**：它直接读 `zjy7/persona.py` 解析出的**真实角色卡**作为 system prompt，
测出来的 prefill 就是真实人设的代价；不带这个参数时脚本会用合成填充文本，只能看趋势、不能当结论。

想扫不同人设长度时再退回 `--persona-chars`：

```bash
python bench_llm.py --model qwen3.5:9b-gguf --persona-chars 1400 --repeat 3   # 现状
python bench_llm.py --model qwen3.5:9b-gguf --persona-chars 300  --repeat 3   # 压缩后
```

**`--cache-test` 怎么判**：

| 加速比 | 结论 |
|---|---|
| ≥5× | ✅ 缓存命中良好，人设 prefill 已被复用 |
| 2–5× | ⚠️ 部分命中，检查三条纪律 |
| <2× | ❌ 基本没命中 |

三条纪律：① 静态人设**逐字节稳定**（差一个空格就整段作废）；② 动态内容（记忆摘要、时间、情绪）一律放 **prompt 末尾**；③ 历史**只追加、不重排、不改写**。

**`--warmup` 的价值**：它模拟的正是本设备的真实用法——用户按键到说完有 2–5 秒，足够网关用一次 `num_predict=1` 的空跑把静态人设 prefill 做完。所以带 `--warmup` 的 TTFT 才是用户实际会遇到的数字。

## 五组对照（对应 §7.6.3 的测量计划）

```bash
# 基线：Q4 + 完整人设 1400 字
python bench_llm.py --model qwen3.5:9b-gguf --persona-chars 1400 --repeat 3 --json r1_base.json

# 对照 1：换 Q5，看质量/速度的交换比
python bench_llm.py --model <q5模型名> --persona-chars 1400 --repeat 3 --json r2_q5.json

# 对照 2：人设压到 300 字，看 prefill 省多少
python bench_llm.py --model qwen3.5:9b-gguf --persona-chars 300 --repeat 3 --json r3_short.json

# 对照 3：Q4 + imatrix（需先用 llama-imatrix 重新量化，见下）
python bench_llm.py --model <imatrix模型名> --persona-chars 300 --repeat 3 --json r4_imatrix.json

# 对照 4：加 draft 模型做投机解码（Ollama 需支持 draft）
python bench_llm.py --model <带draft的模型名> --persona-chars 300 --repeat 3 --json r5_draft.json
```

一次跑多个模型对比：

```bash
python bench_llm.py --compare "qwen3.5:9b-gguf,<q5模型名>" --persona-chars 300 --repeat 3
```

## 输出怎么读

| 列 | 含义 | 对应设备指标 |
|---|---|---|
| `TTFT` | 提问 → 第一个 token | `T_first_audio` 里的 LLM 段 |
| `首句` | 提问 → 出现第一个句末标点 | **≈ `T_first_audio` 的实际起点** |
| `prefill` | 服务端自报的 prompt 处理速度 | 人设长度的代价 |
| `decode` | 服务端自报的生成速度 | 回复长度的代价 |
| `prompt` | 实际 prompt token 数 | 用来核对人设字数换算 |

**判读要点**：

- `prefill` 和 `decode` 取**服务端自报值**（`prompt_eval_duration` / `eval_duration`），不受客户端网络抖动影响，可以横向比；
- `TTFT` 是**端到端实测**，含排队和网络，是真正体感；
- 跑完会打印 `/api/ps`：**必须看到 `size_vram ≈ size`**，否则说明有部分在 CPU，所有数字都不可信；
- `decode` 的理论上限 = 显存带宽 ÷ 权重体积。5070 是 672 GB/s，9B Q4（5.7GB）理论上限约 **118 tok/s**，实测 96 已到 81%。

## imatrix 量化的做法（对照 3 需要）

```bash
# 1. 用角色语料算重要性矩阵（zjy5 台词 / zjy7 train_data.json 都行）
llama-imatrix -m base-f16.gguf -f calibration.txt -o persona.imatrix

# 2. 用矩阵重新量化
llama-quantize --imatrix persona.imatrix base-f16.gguf out-q4-imatrix.gguf Q4_K_M

# 3. 导入 Ollama
printf 'FROM %s\n' "$(pwd)/out-q4-imatrix.gguf" > Modelfile
ollama create qwen3.5-9b-imatrix -f Modelfile
```

校准集**必须用同角色、同语域的语料**——这是本项目相对通用方案的真实优势。

## 注意

- 脚本一律传 `think: false`。只有 API 参数生效，prompt 里写 `/no_think` 没用。
- 脚本**显式禁用代理**。本机 `no_proxy` 含畸形值（`[::1]`），走默认 opener 会把 127.0.0.1 也代理掉，报的错完全看不出原因。
- 首次运行会有模型加载耗时（5–15s），体现在第一轮的 `TTFT` 上。**先跑一次预热再正式统计**，或看汇总里的"首轮模型加载耗时"。
