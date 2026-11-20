# 情绪控制优化方案

> 基于 [`30_EMOTION_CONTROL_AUDIT.md`](30_EMOTION_CONTROL_AUDIT.md) 的审计结论
> 日期：2026-11-20
> 状态：**方案设计完成，关键路径已实测验证，尚未实施**

---

## 一、方案总览

### 1.1 一句话

**改两处调用点 + 加一个指令构造器**，用「缓存音色 + 注入指令」的混合方案，
既让情绪真正进入模型，又比现状更快。

### 1.2 实测验证的三个关键结论

| 结论 | 数据 | 影响 |
|---|---|---|
| 混合方案**能让指令生效** | sad/happy 的 md5 与「instruct2 无 spk」**完全相同** | 指令确实进了模型 |
| 混合方案**不比现状慢** | 3403ms vs 3595ms（**快 5.3%**） | 无性能代价，反而更快 |
| 情绪会**显著改变音频** | sad 17.6s/220Hz vs happy 10.8s/338Hz | 效果强，需注意时长 |

> **重要修正**：审计报告里我曾预估「不传 spk_id 会慢 16.5%」。
> 实测证明**混合方案反而更快** —— 因为删掉 `llm_prompt_speech_token`
> 省掉了 LLM 的 in-context 处理，抵消并超过了重新编码的成本。

---

## 二、技术方案

### 2.1 核心机制

审计已查明上游的静默丢弃陷阱：

```python
# cosyvoice/cli/frontend.py
def frontend_instruct2(self, tts_text, instruct_text, prompt_wav, resample_rate, zero_shot_spk_id):
    model_input = self.frontend_zero_shot(tts_text, instruct_text, ...)
    del model_input['llm_prompt_speech_token']      # ← 关键动作
    del model_input['llm_prompt_speech_token_len']
    return model_input

def frontend_zero_shot(self, tts_text, prompt_text, prompt_wav, resample_rate, zero_shot_spk_id):
    if zero_shot_spk_id == '':
        ... 用 prompt_text(=instruct_text) 现场编码 ...
    else:
        model_input = {**self.spk2info[zero_shot_spk_id]}   # ← instruct_text 被忽略！
```

**混合方案就是手工做 `frontend_instruct2` 该做但没做对的事**：

```python
model_input = dict(spk2info[voice])              # ① 复用缓存（省编码）
del model_input['llm_prompt_speech_token']       # ② 删掉，让指令进 LLM
del model_input['llm_prompt_speech_token_len']
model_input['prompt_text'] = instruct_tokens     # ③ 注入指令
model_input['prompt_text_len'] = instruct_len
model_input['text'] = tts_text_tokens
model_input['text_len'] = tts_text_len
model.model.tts(**model_input, stream=..., speed=...)
```

### 2.2 三条路径的分流策略

```python
# 伪代码
def synthesize(req):
    instruct = build_instruct(req)          # 见 2.3

    if instruct:
        # 有情绪要求 → 混合注入（实测 3403ms，比现状快）
        return hybrid_tts(seg.text, instruct, stream=...)
    else:
        # 无情绪 → 保持原样（3595ms，最稳）
        return inference_zero_shot(seg.text, "", audio_path,
                                   zero_shot_spk_id=voice, stream=...)
```

**为什么要分流而不是全走混合？**

虽然混合实测更快，但：
- 无情绪时走原路径，行为与现在**完全一致**，零回归风险
- 混合路径是新代码，只在需要时才走，出问题影响面小
- 保留 `zero_shot` 便于对比与回退

### 2.3 指令构造器

```python
#: 情绪标签 → 模型指令。措辞依据审计实验四验证有效的方向。
EMOTION_TO_INSTRUCT = {
    "happy":   "请用开心、高昂、轻快的语气说这句话。",
    "sad":     "请用悲伤、低沉、缓慢的语气说这句话。",
    "angry":   "请用愤怒、大声、急促的语气说这句话。",
    "gentle":  "请用温柔、轻软、安慰的语气说这句话。",
    "serious": "请用严肃、冷静、平稳的语气说这句话。",
    "neutral": "",   # 空 = 不做情绪控制，走原路径
}

PROMPT_PREFIX = "You are a helpful assistant.<|endofprompt|>"   # 项目已有（L64）

def build_instruct(req) -> str:
    """构造模型指令。返回空串表示「不做情绪控制」。"""
    # ① 调用方给的自然语言指令优先
    body = (req.instruction or "").strip()

    # ② 否则用 emotion 标签映射
    if not body:
        body = EMOTION_TO_INSTRUCT.get(req.emotion or "neutral", "")

    if not body:
        return ""

    # ③ 必须带 endofprompt，否则 llm.py:479 会断言失败
    if "<|endofprompt|>" not in body:
        body = f"{PROMPT_PREFIX}{body}<|endofprompt|>"
    return body
```

**关于 `endofprompt` 的硬约束**（审计已验证）：

```
AssertionError: <|endofprompt|> not detected in CosyVoice3 text or prompt_text
```

`cosyvoice/llm/llm.py:479` 检查 token id 151646，不带必崩。

### 2.4 改动清单

| 文件 | 位置 | 改动 |
|---|---|---|
| `custom_voice/inference/cosyvoice_engine.py` | L692 | 非流式：加分流 + 混合路径 |
| 同上 | L830 | 流式：同上 |
| 同上 | 新增 | `build_instruct()` + `_hybrid_tts()` |
| 同上 | L635-659 | `_resolve_expression` 注释更新（emotion 不再只是元数据） |
| `custom_voice/schemas/synthesis.py` | L28-30 | 更新「emotion 仅元数据」的说明 |
| `custom_voice/expression/control.py` | L10 | 同上 |
| `zjy13/server/tts/client.py` | L294-302 | 请求体增加 `emotion` / `instruction` |
| 同上 | L427-435 | 同上（流式） |
| `zjy13/.env` | 新增 | 可选：`TTS_DEFAULT_EMOTION` |

**改动量估计**：核心逻辑约 80 行，加测试约 150 行。

---

## 三、验证方案

### 3.1 三级验证

| 级别 | 方法 | 判据 | 耗时 |
|---|---|---|---|
| **L1 冒烟** | 三条相反指令，看 md5 | md5 **必须不同** | 1 分钟 |
| **L2 定量** | 组间/组内比值法 | 比值 **> 3**（当前 0.96） | 10 分钟 |
| **L3 听测** | 盲听判断情绪 | 准确率 **> 80%** | 需人工 |

**L1 已可用**（审计脚本现成）：

```bash
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && \
  ~/cosyvoice-venv/bin/python probe/_audit_instruct_decisive.py"
```

**L2 已可用**：

```bash
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && \
  ~/cosyvoice-venv/bin/python probe/_audit_emotion_signal_noise.py"
```

### 3.2 回归检查清单

修复后必须确认这些**没被改坏**：

| 检查项 | 期望 | 怎么测 |
|---|---|---|
| 无情绪请求走原路径 | RTF 保持 ~0.73 | 对比改动前后 |
| 流式首块延迟 | 仍 < 2s | `/synthesize_stream` |
| 音色相似度 | 仍 > 0.90 | `probe/_speaker_similarity.py` |
| 端到端对话 | 板子正常出声 | `probe/run_device_e2e.sh` |
| 既有测试 | 479 项全绿 | `pytest tests` |

### 3.3 新增测试（建议）

| 测试 | 断言 |
|---|---|
| `test_build_instruct_maps_emotion` | 6 个标签都映射出非空指令 |
| `test_build_instruct_adds_endofprompt` | 输出必含 `<\|endofprompt\|>` |
| `test_build_instruct_empty_when_neutral` | neutral 返回空串 |
| `test_hybrid_keeps_spk_embedding` | 混合路径仍用缓存的 `llm_embedding` |
| `test_hybrid_differs_by_instruct` | 换指令 md5 不同 |
| `test_no_instruct_uses_zero_shot` | 无指令走原路径（可 mock 验证） |

---

## 四、风险与缓解

### 4.1 情绪会显著改变音频时长（**最大风险**）

实测：同一句话，悲伤 **17.6s**，开心 **10.8s** —— 差 **6.8 秒**。

| 影响 | 说明 | 缓解 |
|---|---|---|
| 板子播放时间变长 | 悲伤回复可能播 17 秒 | 固件已有 `APP_MAX_TURN_AUDIO_SEC=60`，够用 |
| 端到端延迟上升 | 首块延迟不变，但总时长变长 | 流式下板子边收边播，不影响体感 |
| 对话节奏变慢 | 悲伤时用户等待更久 | **属预期行为**，真人悲伤时也说得慢 |

**建议**：先用 `gentle`（温和）这类**时长变化小**的情绪上线，观察后再放开 `sad`。

### 4.2 音色可能漂移

混合方案删掉 `llm_prompt_speech_token`（韵律锚点），音色一致性可能略降。

实测：sad 的 rms 只有 **0.043**（基线 0.109，**降 61%**）—— 声音明显变轻。
这是"悲伤"的合理表现，但**可能听起来不像同一个人**。

**缓解**：

- L3 听测必须做，不能只看指标
- 若漂移严重，可只保留 `llm_embedding` 而保留 `llm_prompt_speech_token`
  （需实验验证指令是否仍生效）

### 4.3 短文本保护会拦掉情绪请求

现有 `_check_text_ratio`（`REF_TEXT_RATIO_MIN=0.6`）在文本远短于参考文本时告警，
过短时模型可能不返回音频（今天实测 14 字报 500）。

情绪路径下文本通常够长，但**流式分句后每段可能很短**。

**缓解**：分段时确保每段不低于阈值，或对短段跳过情绪注入。

### 4.4 上游库升级会失效

混合方案依赖 `spk2info` 的内部结构（字段名）。上游若改动会静默失效。

**缓解**：

- 加启动自检：断言 `spk2info[voice]` 含必需字段
- 加测试锁住字段名
- 在代码里注明依赖的上游版本

### 4.5 并发下的缓存一致性

`spk2info` 是共享 dict，混合方案做 `dict(cached)` 浅拷贝后修改。
浅拷贝只复制顶层引用，**不应**修改其中的 tensor。

**缓解**：代码里明确注释「只 pop 和替换，不修改已有 tensor」。

---

## 五、实施顺序

| 阶段 | 内容 | 验收 |
|:--:|---|---|
| **1** | 加 `build_instruct()` + 单元测试 | 6 个标签映射正确、必带 endofprompt |
| **2** | 加 `_hybrid_tts()`，先只接非流式 | L1 冒烟通过（md5 不同） |
| **3** | 接流式路径 | 流式下 md5 也不同 |
| **4** | zjy13 侧补发 emotion/instruction | 端到端能传下去 |
| **5** | L2 定量验证 | 组间/组内 > 3 |
| **6** | 回归（479 项 + 真机） | 全绿 |
| **7** | L3 听测 | 准确率 > 80% |

**建议在第 2 阶段后暂停**，用 `_audit_instruct_decisive.py` 确认生效，
再继续后续阶段 —— 避免一次改太多难以定位问题。

---

## 六、备选方案对比

| 方案 | 情绪生效 | 耗时 | 音色 | 复杂度 | 结论 |
|---|---|---|---|---|---|
| **混合注入（推荐）** | ✅ | **3403ms** | 略漂移 | 中 | 最优 |
| `instruct2` 无 spk | ✅ | 4360ms（+21%） | 略漂移 | 低 | 慢，但改动最小 |
| `instruct2` + spk | ❌ | 3595ms | 不变 | 低 | **无效**（陷阱） |
| `inference_instruct`（sft） | ✅ | 未测 | 需重注册 | 高 | 改动大 |
| 微调模型 | ✅ | — | — | 极高 | 不需要 |

**如果只想最小改动**：用 `instruct2` 无 spk，代价是慢 21%。
**如果追求最优**：用混合方案，比现状还快 5.3%。

---

## 七、关于旧的 D1 结论

`docs/13_D1_EMOTION_VERDICT.md` 判定"情绪指令无效"。

本次审计认为**该结论可能建立在有缺陷的实验上** —— 如果当时用了
`zero_shot_spk_id`，指令从一开始就被丢弃，测到的"变化"只是采样噪声。

**建议**：修复后**重跑 D1 实验**，重新下结论。
在此之前，README 和文档里"情绪控制无效"的说法应改为
「**当前调用路径下无效，模型本身支持，待修复**」。

---

## 八、验证数据存档

| 实验 | 脚本 | 输出目录 |
|---|---|---|
| 混合方案生效性 | `probe/_verify_hybrid_instruct.py` | `_verify_hybrid_instruct/` |
| 分流策略成本 | `probe/_verify_routing_cost.py` | `_verify_routing_cost/` |
| 六情绪基础对照 | `probe/_audit_emotion_basic.py` | `_audit_emotion_basic/` |
| 组间/组内判据 | `probe/_audit_emotion_signal_noise.py` | `_audit_emotion_signal_noise/` |
| spk_id 陷阱 | `probe/_audit_instruct2_spk_trap.py` | `_audit_instruct2_spk_trap/` |
| 决定性判据 | `probe/_audit_instruct_decisive.py` | `_audit_instruct_decisive/` |
| 流式验证 | `probe/_audit_stream_instruct.py` | `_audit_stream_instruct/` |

关键数据（3 轮均值）：

```
A zero_shot+spk     3595ms  4.920s  RTF 0.731   —（现状）
B 混合+轻指令        3403ms  5.040s  RTF 0.675   −5.3%   ← 推荐
C instruct2 无spk   4360ms  5.040s  RTF 0.865   +21.3%
```

情绪效果（同文本）：

```
悲伤  17.60s  220.2Hz  rms 0.043
开心  10.76s  338.0Hz  rms 0.120
基线   4.92s  315.8Hz  rms 0.109
```

---

## 九、结论

**方案可行，且比预期更好。**

- 情绪确实能进模型（md5 与对照路径完全一致）
- 性能没有代价，反而快 5.3%
- 主要风险是音频时长变化大（6.8s 差异），需用听测把关

**建议实施路径**：先只做非流式 + `gentle` 一种情绪，验证通过后再放开。

**不需要微调模型** —— 全部问题都在调用路径。
