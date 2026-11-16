# 缺陷修复报告

> 日期：2026-11-16
> 触发：Phase 4 完成后做缺陷审查
> 结果：**修复 6 个缺陷 + 1 个方法论问题**，264 个测试全绿

---

## 一、修复清单

| # | 缺陷 | 类型 | 处置 |
|:-:|---|---|---|
| 1 | `StreamPolicy.reslice` / `chunk_ms` 定义了但从未被消费 | 死字段 | **删除** |
| 2 | `enable_sdpa` 登记但无实现 | 配置摆设 | **删除**（实测无意义） |
| 3 | `enable_vllm_llm` 登记但无实现 | 配置摆设 | **删除**（无可用权重） |
| 4 | `cpu_offload` 登记但无实现 | 配置摆设 | **删除**（无接口） |
| 5 | `enable_compile` 登记但无实现 | 配置摆设 | **实测有 8.7% 收益，已实现** |
| 6 | 6 处未使用的 import | 代码卫生 | **清理** |
| 7 | 并发验收脚本用首次合成当基准 | 测试误判 | **修正** |

---

## 二、审查方法

原提示词有硬性要求："不能留下配置看似生效但实际未使用的参数"。

但 Phase 4 交付时，`CONSUMED_FIELDS` 里有 4 个字段标注为
「Phase 4 优化阶段（当前仅记录到 /system/status）」——Phase 4 已经做完了，
这个标注变成了欠账。

**写了一个自动审查脚本** `probe/_audit_config_usage.py`：

```
1. 遍历 CONSUMED_FIELDS，检查每个字段是否真在 schema.py 之外被引用
2. 检查 StreamPolicy 的字段是否被外部消费
3. 用 AST 解析所有文件，找出导入后从未使用的标识符
```

首次运行结果：

```
[问题] 4 个配置字段未真正被使用：
       - enable_compile
       - enable_sdpa
       - enable_vllm_llm
       - cpu_offload
[问题] StreamPolicy 以下字段未被外部消费：['reslice', 'chunk_ms']
```

---

## 三、逐项处置

### 3.1 StreamPolicy 死字段（删除）

```python
@dataclass
class StreamPolicy:
    enabled: bool = True
    threshold: int = 15
    reslice: bool = False      # ← 只在 to_dict 里出现，从未影响行为
    chunk_ms: int = 80         # ← 同上
```

这两个字段是设计时的设想（服务层二次切分音频块），但从未实现。
留着会让读代码的人以为有这个功能。

**处置**：删除。若将来真需要二次切分，再加回并实现。

**守护测试**：`test_policy_has_no_dead_fields` 断言
`dataclass` 声明的字段集合必须等于 `to_dict()` 的输出键集合。

### 3.2 enable_sdpa（删除，实测无意义）

调查发现：

```
PyTorch SDPA 后端状态:
  flash_sdp_enabled: True
  mem_efficient_sdp_enabled: True
  math_sdp_enabled: True
```

**PyTorch 2.8 默认已启用 SDPA**，而且 CosyVoice 内部也没有 sdpa 开关
（grep 结果：LLM 与 flow 里 `scaled_dot_product_attention` 出现 0 次）。

**处置**：删除。该配置无论设什么值都不会改变行为。

### 3.3 enable_vllm_llm（删除，无可用权重）

调查发现：

```
CosyVoice3.__init__ 签名: (model_dir, load_trt=False, load_vllm=False, fp16=False, ...)
模型目录 /home/user/models/Fun-CosyVoice3-0.5B-2512/vllm 存在: False
```

`load_vllm` 需要先用 `export_cosyvoice2_vllm` 导出专用权重，
模型目录里没有。启用它是独立工作量，不该以"配置项"的形式存在。

**处置**：删除。若将来要做，作为独立任务。

### 3.4 cpu_offload（删除，无接口）

调查发现 CosyVoice3 内部没有任何 offload 相关代码
（`cpu_offload` / `to('cpu')` 出现 0 次）。

手动把子模块 `.to('cpu')` 会破坏推理图，且每次请求都要搬运权重，
延迟会灾难性上升。

**处置**：删除。

### 3.5 enable_compile（实现，实测有收益）

先做了一轮初步测量，结果可疑：

```
A 基线    3.59 / 2.94 / 3.35  中位 3.35
B compile 3.15 / 2.94 / 3.39  中位 3.15
```

第 2、3 次几乎完全相同（2.94 vs 2.94、3.35 vs 3.39），只有第 1 次不同
——那是预热残留，不是真实差异。

**于是做了严谨复测**：同一进程持有两个模型实例，交错执行 A B A B...，
8 轮，取配对差值。

```
轮次      A基线   B_compile      差值
1        2.971s      2.668s   -0.303s
2        3.065s      2.640s   -0.425s
3        2.648s      2.504s   -0.144s
4        3.035s      2.901s   -0.134s
5        3.063s      2.962s   -0.101s
6        2.833s      2.735s   -0.098s
7        3.104s      2.831s   -0.273s
8        3.165s      3.226s   +0.061s

A 基线    中位 3.049s  均值 2.986s  标准差 0.168s
B compile 中位 2.783s  均值 2.808s  标准差 0.224s
配对差值均值 -0.177s（相对改善 8.7%）
噪声参考（基线标准差/2）= 0.084s，收益是其 2 倍以上
8 轮中 7 轮更快
```

**结论：确实有收益。** 实现方式：

```python
def _apply_compile(self) -> None:
    """只编译 LLM 子模块 —— 它是自回归瓶颈（占首包约 52%）。
    不编译 flow/hift：结构更复杂，graph break 风险高。"""
    inner = getattr(self._model, "model", None)
    llm = getattr(inner, "llm", None)
    inner.llm = torch.compile(llm, dynamic=False)
```

**关键顺序**：必须放在 `_warmup()` 之前，让预热承担首次编译开销，
避免首个真实请求被拖慢。

**降级策略**：compile 失败只记日志，不阻断启动。

**实测验证**（`probe/_verify_compile_switch.py`）：

```
enable_compile = False
  compile_requested = False
  compile_applied   = False
  合成 2.90s  RTF 0.648

enable_compile = True
  compile_requested = True
  compile_applied   = True     ← 真正生效
  合成 2.79s  RTF 0.623
```

`diagnostics` 里区分 `compile_requested`（配置要求）与
`compile_applied`（实际生效），避免"以为生效了"。

### 3.6 未使用 import（清理）

```
config/schema.py:            Any
expression/instruction.py:   EMOTION_LABELS
server/app.py:               time, uuid
server/routes/speakers.py:   ZJY14Error
server/routes/synthesis.py:  json, InvalidRequestError
```

**处置**：全部移除。

> ⚠️ 过程中的教训：我先用 PowerShell 的 `.Replace()` 批量替换，
> 结果 PowerShell 把 `\f` 当成换页转义符，把 `from` 改成了 `rrom`、
> `def` 改成了 `der`、`if` 改成了 `ir`，损坏了 4 个文件共 46 处。
> 最后用 `git checkout` 恢复，改用 `edit` 工具逐个精确修改。
>
> **教训：不要用 PowerShell 做源码文本替换，特别是含反斜杠的模式。**

### 3.7 并发验收脚本的误判（修正）

原脚本用「首次合成耗时」当基准：

```
单请求耗时基准 5.92s      ← 含 CUDA kernel 初始化，虚高 3 倍
3 并发墙钟 7.97s
→ 判定「可能并行」          ← 误判
```

**首次合成含 CUDA 初始化开销**，用它当基准会让 3 并发看起来像并行。

修正：先预热一次排除初始化，再测 3 次取中位作基准。

修正后：

```
已预热（首次合成开销已排除）
单请求中位 2.43s（3 次：[2.38, 2.43, 2.62]）
3 并发墙钟 7.21s，各请求 [2.43, 4.98, 7.21]
墙钟/单请求 = 2.96  → [PASS] 串行执行
```

---

## 四、修复后状态

### 4.1 配置项：21 个，全部有真实消费方

```
auth_token / default_voice_id / device / enable_compile / engine
host / max_concurrent_requests / max_queue_size / memory_safety_margin_mb
min_text_ratio / model_dir / port / precision / queue_timeout_s
request_timeout_s / speaker_cache_enabled / speaker_cache_max_items
stream_char_threshold / stream_enabled / text_frontend / token_hop_len_reset
```

审查脚本输出：`[OK] 所有登记字段都在代码中被使用`

### 4.2 测试

```
264 个全绿（修复前 257 + 新增 7 个守护测试）
```

新增守护测试：

| 测试 | 守护什么 |
|---|---|
| `test_policy_has_no_dead_fields` | StreamPolicy 不能有死字段 |
| `test_compile_disabled_by_default` | 默认不编译 |
| `test_compile_switch_is_consumed` | compile 配置真生效 |
| `test_compile_failure_degrades_gracefully` | compile 失败不阻断启动 |
| `test_removed_switches_stay_removed` × 3 | 已删的 3 个开关不回归 |

### 4.3 端到端

11 项验收全通过，其中并发项从 WARN 变为 PASS（比值 2.96）。

---

## 五、经验教训

1. **配置项是最容易积累技术债的地方**。登记时写"Phase X 实现"很轻松，
   但阶段结束后就成了欠账。应该用自动化审查定期检查，而不是靠记忆。

2. **"登记"不等于"实现"**。`CONSUMED_FIELDS` 只保证字段名写在了某处，
   不保证真的影响行为。审查脚本必须检查"是否被 schema.py 之外的文件引用"。

3. **实测才能决定保留还是删除**。`enable_compile` 我原本倾向删除
   （初测收益在噪声内），但严谨复测后发现确实有 8.7%。
   如果凭初测结论就删，会丢掉一个真实优化。

4. **交错采样是排除顺序效应的必要手段**。同一进程里 A B A B 交替，
   比"先跑完 A 再跑完 B"可靠得多。

5. **测试基准要考虑初始化开销**。首次合成含 CUDA 初始化，虚高 3 倍。
   用它当基准会把"串行"误判为"并行"。

6. **不要用 PowerShell 做源码替换**。反斜杠会被当转义符，
   `\f` → 换页符会静默破坏代码。用 `edit` 工具或 Python 脚本。

---

## 六、复现命令

```bash
# 配置消费审查（核心工具）
wsl -e bash -lc "source ~/cosyvoice-venv/bin/activate && \
  cd /mnt/d/xiangmudaima/zjy14 && PYTHONPATH=. python probe/_audit_config_usage.py"

# 4 个开关的可实现性调查
wsl -e bash probe/run_d1.sh probe/_audit_opt_switches.py

# torch.compile 初步测量
wsl -e bash probe/run_d1.sh probe/_p4_opt_measure.py

# torch.compile 严谨复测（交错采样）
wsl -e bash probe/run_d1.sh probe/_p4_compile_rigorous.py

# compile 开关生效验证
wsl -e bash probe/run_d1.sh probe/_verify_compile_switch.py

# 测试
wsl -e bash scripts/run_tests.sh

# 端到端验收
wsl -e bash scripts/verify_phase4.sh
```
