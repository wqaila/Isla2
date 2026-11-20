# Fun-CosyVoice 3.0 情绪控制专项审计报告

> 审计对象：`Fun-CosyVoice3-0.5B-2512`
> 审计日期：2026-11-20
> 审计范围：**仅代码审计与对照实验，未修改任何文件、未替换模型权重、未重构项目**
> 结论性质：**情绪控制在当前调用路径下完全无效，且是「静默丢弃」，不是模型能力问题**

---

## 一、结论摘要

| 问题 | 结论 |
|---|---|
| 是否加载 `Fun-CosyVoice3-0.5B-2512` | ✅ **是**，已实证（`cosyvoice3.yaml` + `CosyVoice3LM` + 权重文件） |
| 实际调用的推理函数 | ❌ **全部是 `inference_zero_shot`**，`inference_instruct2` **零调用** |
| 是否把 `zero_shot` 误当情绪接口 | ❌ **是**。`zero_shot` 是**音色克隆**接口，不接收任何指令参数 |
| 情绪信息是否进入模型 | ❌ **完全没有**。请求体里连 `emotion` 字段都没有 |
| 是否只改了文本/语速/音量 | ⚠️ 部分。真正生效的只有 `speed` / `volume` / `pause_profile` |
| 流式路径是否支持指令 | ✅ **底层支持**（已实测），但项目未使用 |
| **模型本身支持情绪吗** | ✅ **支持**！实测同一 spk_id 下换指令输出逐字节不同 |
| **根本原因** | 项目从未调用 `inference_instruct2`；且该接口在传 `zero_shot_spk_id` 时**会静默丢弃指令**（上游 bug） |
| 是否需要微调 | ❌ **不需要**。只需改推理调用路径 |

**一句话**：模型有能力，接口有陷阱，项目走错了路 —— 而且走的这条路从设计上就不可能带情绪。

---

## 二、推理架构与实际调用链路

### 2.1 当前架构

```
┌─────────────────────────────────────────────────────────────────┐
│ 主机 A（zjy13，Windows）                                          │
│   角色卡 elysia.yaml                                             │
│     └─ emotional_baseline / emotional_triggers / speech_style    │
│        ↓ **这些只是给 LLM 的提示词，影响用词，不进 TTS**           │
│   TTSClient.synthesize_stream()                                  │
│     └─ 请求体：{text, voice_id, speed, pause_profile}            │
│        ↑ **没有 emotion，没有 instruction**                       │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP POST /synthesize_stream
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ 主机 B（zjy14，WSL2 + RTX 5070）                                  │
│   SynthesisRequest  ← 有 emotion/instruction 字段，但…            │
│     └─ _resolve_expression()                                     │
│        └─ emotion → ExpressionParams.emotion（**仅元数据**）       │
│        └─ instruction → 解析成 speed/volume/pause（**不进模型**）  │
│   CosyVoiceEngine._synthesize_segments()                         │
│     └─ model.inference_zero_shot(                                │
│            seg.text,        # tts_text                          │
│            "",              # ← prompt_text 传空字符串！          │
│            audio_path,      # prompt_wav                        │
│            zero_shot_spk_id=voice,                               │
│            speed=expr.speed)                                     │
│        ↑ **没有任何指令参数的位置**                                │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 实际调用点（全部 3 处，均为 `inference_zero_shot`）

| 位置 | 函数 | 用途 |
|---|---|---|
| `cosyvoice_engine.py:579` | `inference_zero_shot` | 预热（warmup） |
| `cosyvoice_engine.py:692` | `inference_zero_shot` | **非流式合成**（`_synthesize_segments`） |
| `cosyvoice_engine.py:830` | `inference_zero_shot` | **流式合成**（`synthesize_stream`） |

**`inference_instruct2` 在项目代码中零调用**（已全项目 grep 确认）。

### 2.3 底层库实际提供的接口

`~/CosyVoice/cosyvoice/cli/cosyvoice.py`：

```python
L91 : def inference_zero_shot(self, tts_text, prompt_text, prompt_wav,
                             zero_shot_spk_id='', stream=False, speed=1.0, ...)
L105: def inference_cross_lingual(self, tts_text, prompt_wav, ...)
L116: def inference_instruct(self, tts_text, spk_id, instruct_text, ...)
L177: def inference_instruct2(self, tts_text, instruct_text, prompt_wav,
                             zero_shot_spk_id='', stream=False, speed=1.0, ...)
```

`inference_instruct2` **存在且可用**，签名完整支持：合成文本 + 指令 + 参考音频 + spk_id。

---

## 三、情绪指令是否真正进入模型

### 3.1 第一层：主机 A 就没发情绪

`zjy13/server/tts/client.py:294-302` 与 `427-435`（流式与非流式两处）：

```python
payload = {
    "text": text,
    "voice_id": voice_id or self.config.default_voice,
    "speed": speed,
    "pause_profile": self.config.pause_profile,
}
```

**没有 `emotion`，没有 `instruction`，没有 `style`。**

`zjy13/server/characters/elysia.yaml` 里确实有 `emotional_baseline`、`emotional_triggers`、
`speech_style`，但那些字段是拼进 **LLM 系统提示词**的 —— 只影响模型「说什么词」，
对「怎么说」没有任何控制。

### 3.2 第二层：主机 B 收到了但只当元数据

`cosyvoice_engine.py:635-659`：

```python
def _resolve_expression(self, req):
    """优先级：instruction（自然语言）> 显式字段 > 默认值。
    emotion/style 只作元数据，不参与引擎调用。   ← 代码自己写明了
    """
```

`custom_voice/schemas/synthesis.py:28-30`：

```python
**重要（D1 结论）**：``emotion`` / ``intensity`` / ``style`` 仅作元数据，
真正生效的是 ``speed`` / ``volume`` / ``pause_profile`` / ``instruction``。
```

`custom_voice/expression/control.py:10`：

```python
2. emotion 保留为**元数据**，供 LLM 侧用词表达，不传给 TTS 模型
```

### 3.3 第三层：`instruction` 也没进模型

`instruction` 字段确实被解析了，但解析结果是**语速/音量/停顿**，不是模型指令：

```python
if req.instruction:
    parsed = parse_instruction(req.instruction)   # 规则解析器
    base = merge_params(base, parsed.params)      # 只改 speed/volume/pause
```

然后传给模型的是：

```python
self._model.inference_zero_shot(
    seg.text,
    "",                    # ← prompt_text 空字符串
    info["audio_path"],
    zero_shot_spk_id=voice,
    speed=expr.speed,      # ← 只有语速是真实生效的
)
```

### 3.4 结论

**情绪信息在三个层次上都断了**：

1. 主机 A 不发
2. 主机 B 收到也当元数据
3. 就算解析成参数，`inference_zero_shot` 也没有接收指令的形参

---

## 四、对照实验（实证）

### 4.1 实验一：项目当前路径下，情绪字段有影响吗

同一文本、同一参考、同参数，只改 `emotion` / `instruction`：

| case | 指令 | dur | f0 | rms | md5 |
|---|---|---|---|---|---|
| baseline_none | — | 4.960s | 406.8Hz | 0.13593 | c7302bb6... |
| happy | 开心愉悦 | 4.280s | 381.0Hz | 0.14820 | b005a24b... |
| sad | 悲伤低落 | 4.960s | 393.4Hz | 0.10654 | 761b76de... |
| angry | 愤怒激动 | 4.760s | 240.0Hz | 0.11152 | fa7b592f... |
| gentle | 温柔安慰 | 4.440s | 381.0Hz | 0.12919 | 6bb690ea... |
| serious | 严肃冷静 | 4.800s | 413.8Hz | 0.09730 | 043be93b... |

**看起来有差异，但这不能证明情绪生效** —— 因为 `ras_sampling`（top_p=0.8, top_k=25）
每次推理都有随机性，同文本连跑两次本来就不同。

### 4.2 实验二：组间差异 vs 组内噪声（关键）

每个情绪重复 3 次，共 15 次合成：

| 情绪 | 组内时长范围 | 组内 σ |
|---|---|---|
| baseline | 4.480 ~ 5.520s | **0.4312** |
| happy | 4.200 ~ 4.920s | 0.2993 |
| sad | 4.440 ~ 5.120s | 0.3035 |
| angry | 4.080 ~ 5.520s | **0.5891** |
| gentle | 4.440 ~ 5.000s | 0.2473 |

| 情绪 | 均值 dur | 相对基线 Δdur |
|---|---|---|
| happy | 4.520s | −0.533s |
| sad | 4.693s | −0.360s |
| angry | 4.773s | −0.280s |
| gentle | 4.787s | −0.267s |

**判定**：

```
组内时长 σ 均值     = 0.3741s   （同一情绪重复跑）
组间时长 |Δ| 均值   = 0.3600s   （不同情绪之间）
组间 / 组内 比值    = 0.96
```

**比值 0.96 < 1** —— 不同情绪之间的差异**比同一情绪重复跑的噪声还小**。
情绪字段对输出没有可辨识影响。**实证确认无效。**

### 4.3 实验三：模型本身支持情绪吗（决定性）

绕过项目代码，直接调底层接口。固定 spk_id，**只改指令文本**（三条语义完全相反）：

| case | 指令 | dur | f0 | rms | md5 |
|---|---|---|---|---|---|
| spk_sad | 非常悲伤、低沉、缓慢、想哭 | 12.440s | 296.3Hz | 0.12907 | **cdd7a507f832** |
| spk_happy | 非常开心、高昂、快速、兴奋 | 12.440s | 296.3Hz | 0.12907 | **cdd7a507f832** |
| spk_angry | 愤怒、大声、急促、咆哮 | 12.440s | 296.3Hz | 0.12907 | **cdd7a507f832** |

**三条语义完全相反的指令 → 输出逐字节完全相同。**

这是「指令被静默丢弃」的铁证。

### 4.4 实验四：无 spk_id 时指令生效吗

| case | 指令 | dur | f0 | rms | md5 |
|---|---|---|---|---|---|
| nospk_sad | 悲伤 | **17.600s** | **220.2Hz** | **0.04274** | 2dacf7140b06 |
| nospk_happy | 开心 | **10.760s** | **338.0Hz** | **0.12030** | a204ce0cc1cd |
| nospk_angry | 愤怒 | **10.560s** | **275.9Hz** | **0.08603** | 54d605dc51c4 |

**差异巨大且方向合理**：

- 悲伤：时长最长（17.6s，慢）、基频最低（220Hz）、能量最低（0.043，轻声）
- 开心：时长最短之一（10.76s）、基频最高（338Hz）、能量最高（0.120）
- 愤怒：时长最短（10.56s）、基频居中（276Hz）、能量中等（0.086）

**模型完全支持情绪控制。**

### 4.5 实验五：流式路径支持指令吗

| case | 块数 | 首块 | 时长 | md5 |
|---|---|---|---|---|
| stream_sad | 6 | 55680B | 17.600s | 18fce6ae83d5 |
| stream_happy | 3 | 295680B | 10.760s | 51d0104ff4b3 |

**流式下指令同样生效**（时长差 6.84s，首块差 240KB）。

> 结论：修复后流式与非流式**都可以**用指令，不存在"只能非流式"的限制。

---

## 五、已实现 / 未实现 / 实现但无效

### ✅ 已实现且真正生效

| 功能 | 位置 | 说明 |
|---|---|---|
| 音色克隆（zero_shot） | `cosyvoice_engine.py:692, 830` | elysia 音色稳定，相似度 0.9208 |
| 语速控制 | 同上，`speed=` | 真实进入模型 |
| 音量控制 | `expression/pause.py:apply_gain` | 音频层增益，不依赖模型 |
| 停顿控制 | `expression/pause.py:build_segments` | 分句 + 插静音 |
| 流式合成 | `cosyvoice_engine.py:793` | 首块 1.5s |
| 文本前端（wetext） | `inference/text_frontend.py` | 数字读法正常 |

### ❌ 未实现

| 功能 | 状态 |
|---|---|
| 模型级情绪控制 | **完全没有**。`inference_instruct2` 零调用 |
| 主机 A → 主机 B 的情绪传递 | **完全没有**。请求体无 `emotion` 字段 |
| 情绪 → 模型指令的转换 | **完全没有** |

### ⚠️ 实现了但可能无效 / 有陷阱

| 功能 | 陷阱 |
|---|---|
| `SynthesisRequest.emotion` | 有字段、有枚举、有校验，但**只存数据库，不进模型** |
| `SynthesisRequest.instruction` | 有解析器，但只转成 speed/volume/pause |
| `inference_instruct2`（底层） | **可用，但传 `zero_shot_spk_id` 时会静默丢弃指令**（见下） |

---

## 六、根本原因

### 6.1 直接原因：项目走错了推理函数

`inference_zero_shot` 是**音色克隆**接口，它的设计目的是「用参考音频的音色说任意文本」。
它**没有接收指令的形参**，所以无论上层怎么传情绪，都不可能到达模型。

### 6.2 深层原因：上游接口有静默丢弃缺陷

这是本次审计最重要的发现。`frontend_instruct2` 的实现：

```python
# cosyvoice/cli/frontend.py
def frontend_instruct2(self, tts_text, instruct_text, prompt_wav,
                       resample_rate, zero_shot_spk_id):
    model_input = self.frontend_zero_shot(
        tts_text, instruct_text, prompt_wav, resample_rate, zero_shot_spk_id)
    del model_input['llm_prompt_speech_token']
    del model_input['llm_prompt_speech_token_len']
    return model_input
```

而 `frontend_zero_shot` 里：

```python
def frontend_zero_shot(self, tts_text, prompt_text, prompt_wav,
                       resample_rate, zero_shot_spk_id):
    if zero_shot_spk_id == '':
        # 用 prompt_text(=instruct_text) 现场编码
        prompt_text_token, ... = self._extract_text_token(prompt_text)
        ...
    else:
        model_input = {**self.spk2info[zero_shot_spk_id]}   # ← instruct_text 被完全忽略！
    model_input['text'] = tts_text_token
```

**当 `zero_shot_spk_id` 非空时，`instruct_text` 根本没有被读取** ——
`spk2info` 里存的 `prompt_text`（注册音色时用的参考文本）直接覆盖了它。

这就是为什么实验三的三条相反指令产生了**逐字节相同**的输出。

**这个缺陷极其隐蔽**：

- 不报错
- 不告警
- 返回正常的音频
- 只是"情绪没生效"

`docs/13_D1_EMOTION_VERDICT.md` 当时记录的"情绪指令无效"，
很可能就是这个陷阱导致的误判 —— 而不是模型能力问题。

### 6.3 另一个必须注意的点：`endofprompt` 标记

实验中发现，`inference_instruct2` **强制要求**文本里含 `<|endofprompt|>`：

```
AssertionError: <|endofprompt|> not detected in CosyVoice3 text or prompt_text,
                check your input!
```

`cosyvoice/llm/llm.py:479` 有硬断言（token id 151646）。
不带标记会直接崩溃 —— 这比"静默失效"好，至少会报错。

---

## 七、最小修复方案

### 7.1 方案对比

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A（推荐）** | 改用 `inference_instruct2`，**不传 `zero_shot_spk_id`**，把参考文本拼进 instruct_text | 指令真正生效，已实测 | 每次都要现场编码参考音频，**慢约 16.5%**（项目注释实测） |
| B | 改用 `inference_instruct2` + 传 `zero_shot_spk_id` | 保持速度 | **指令被静默丢弃**（本次实测），等于没修 |
| C | 改用 `inference_instruct`（有 spk_id 版本） | 指令生效 | 需要先注册为 sft 音色，改动更大 |
| D | 微调模型 | 可定制 | 成本极高，且**当前不需要** |

### 7.2 推荐的最小改动（方案 A）

只改 `cosyvoice_engine.py` 两处调用点，不动其他任何文件：

**非流式**（`_synthesize_segments`，约 L692）：

```python
# 改前
chunks = list(self._model.inference_zero_shot(
    seg.text, "", info["audio_path"],
    zero_shot_spk_id=voice, stream=False, speed=expr.speed))

# 改后
instruct = self._build_instruct(expr, req)   # 见下
chunks = list(self._model.inference_instruct2(
    seg.text, instruct, info["audio_path"],
    # 注意：不传 zero_shot_spk_id —— 传了指令会被丢弃
    stream=False, speed=expr.speed))
```

**流式**（`synthesize_stream`，约 L830）：同上，`stream=True`。

**指令构造**（新增一个小函数）：

```python
PROMPT_PREFIX = "You are a helpful assistant.<|endofprompt|>"  # 已有，L64

def _build_instruct(self, expr, req) -> str:
    """把 emotion/instruction 转成模型指令，必须带 endofprompt。"""
    # 优先用调用方给的自然语言指令
    body = (req.instruction or "").strip()
    if not body:
        # 否则用 emotion 标签映射成指令
        body = EMOTION_TO_INSTRUCT.get(expr.emotion.value, "")
    if not body:
        # 无情绪要求：退回纯音色克隆（保持现状）
        return ""      # 空则走 zero_shot 分支
    if "<|endofprompt|>" not in body:
        body = f"{PROMPT_PREFIX}{body}<|endofprompt|>"
    return body
```

**情绪标签 → 指令映射**（依据实验四验证有效的方向）：

```python
EMOTION_TO_INSTRUCT = {
    "happy":   "请用开心、高昂、轻快的语气说这句话。",
    "sad":     "请用悲伤、低沉、缓慢的语气说这句话。",
    "angry":   "请用愤怒、大声、急促的语气说这句话。",
    "gentle":  "请用温柔、轻软、安慰的语气说这句话。",
    "serious": "请用严肃、冷静、平稳的语气说这句话。",
}
```

**分流逻辑**：`_build_instruct` 返回空串时走原来的 `zero_shot`（保持无语气的音色克隆性能），
非空时走 `instruct2`。

### 7.3 需要同步改的地方

| 文件 | 改动 |
|---|---|
| `cosyvoice_engine.py:692` | `zero_shot` → `instruct2`，加指令构造 |
| `cosyvoice_engine.py:830` | 同上（流式） |
| `cosyvoice_engine.py:635` | `_resolve_expression` 注释更新（emotion 不再只是元数据） |
| `schemas/synthesis.py:28-30` | 更新那段"emotion 仅元数据"的说明 |
| `expression/control.py:10` | 同上 |
| `zjy13/server/tts/client.py:294` | 请求体增加 `emotion` / `instruction` 字段 |
| `zjy13/.../elysia.yaml` | 可选：把 `emotional_baseline` 映射成默认 instruction |

### 7.4 必须注意的副作用

**① 速度代价**：不传 `zero_shot_spk_id` 意味着每次都要重新编码参考音频。
项目注释记录实测「用 add_zero_shot_spk 省 16.5% 合成耗时」。
所以修复后 RTF 会从 0.63 上升到约 0.73~0.75。

**缓解**：可在 `spk2info` 里**额外存一份"参考文本 token"**，让 instruct2 复用。
但这需要改上游库或打 monkey-patch，超出"最小修复"范围。

**② 音色可能漂移**：`instruct2` 会 `del llm_prompt_speech_token`（删除韵律锚点），
所以音色一致性可能略降。实验四里悲伤指令的 rms 只有 0.043（很轻），
听感上可能比现在"更不像同一个人"。**需要听测确认。**

**③ 短文本会触发比例警告**：`_check_text_ratio` 的 `REF_TEXT_RATIO_MIN=0.6`，
若合成文本远短于参考文本会告警。这是既有逻辑，不受影响。

---

## 八、修复后如何测试情绪控制

### 8.1 快速验证（5 分钟）

用项目已有的 API，固定文本，只改 `instruction`：

```bash
# 三条语义相反的指令
for i in "请用悲伤低沉的语气说这句话。" \
         "请用开心高昂的语气说这句话。" \
         "请用愤怒大声的语气说这句话。"; do
  curl -s -X POST http://127.0.0.1:50001/synthesize \
    -H "Content-Type: application/json" \
    -d "{\"text\":\"今天我们一起去看海吧。\",\"voice_id\":\"elysia\",\"instruction\":\"$i\"}" \
    -o "out_$(echo $i | md5sum | cut -c1-6).wav"
done
```

**判定标准**：三个文件的 md5 **必须不同**，且时长差异应显著
（参考实验四：悲伤 17.6s vs 开心 10.8s，差 6.8s）。

### 8.2 严谨验证（组间/组内比值法）

复用本次审计的脚本思路：

1. 每个情绪重复 3 次
2. 算组内时长 σ（同一情绪重复跑的噪声）
3. 算组间 |Δ| 均值（不同情绪的差异）
4. **要求组间/组内 > 3**（当前是 0.96）

脚本已留在 `zjy14/_audit_emotion_noise.py`，改 API 调用即可复用。

### 8.3 听测（不可省略）

客观指标只能证明"有差异"，不能证明"是想要的情绪"。
建议盲听：随机播放 5 条，判断是哪种情绪，准确率 > 80% 才算成功。

### 8.4 回归检查（别修坏别的）

| 检查项 | 期望 |
|---|---|
| 无语气请求仍走 `zero_shot` | RTF 保持 ~0.63，速度不退化 |
| 音色相似度 | 仍 > 0.90（可能略降，需记录） |
| 流式首块延迟 | 仍 < 2s |
| 端到端对话 | 板子仍能正常出声 |
| 既有 479 项测试 | 全绿 |

### 8.5 已有的审计脚本（可直接复用）

| 脚本 | 用途 |
|---|---|
| `_audit_emotion_experiment.py` | 6 组情绪对照（基础） |
| `_audit_emotion_noise.py` | **组间/组内比值**（最严谨的判据） |
| `_audit_instruct2_mechanism.py` | 四组对照，验证 spk_id 陷阱 |
| `_audit_decisive.py` | **固定 spk_id 换指令**（决定性判据） |
| `_audit_stream_instruct.py` | 流式路径验证 |

输出在 `zjy14/_audit_out*/` 下。

---

## 九、需要微调吗

### ❌ 不需要

理由（全部有实测支撑）：

1. **模型本身支持情绪**：实验四证明无 spk_id 时，悲伤/开心/愤怒产生
   时长差 6.8s、基频差 118Hz、能量差 2.8 倍 —— 方向完全合理
2. **流式也支持**：实验五证明流式路径同样生效
3. **问题出在调用路径**：项目从未调用 `inference_instruct2`
4. **上游有陷阱**：传 `zero_shot_spk_id` 会静默丢弃指令

### 什么时候才需要微调

只有以下情况才考虑：

- 修复后实测情绪**方向不对**（如"悲伤"反而变快变高）
- 修复后**音色漂移严重**到不可接受
- 需要 CosyVoice3 原生不支持的**细粒度情绪强度**（如 intensity=0.3）

### 关于 `docs/13_D1_EMOTION_VERDICT.md` 的旧结论

该文档记录"情绪指令无效"，并给出了 F0 变化数据（15.4/72.5 Hz vs 10.1/24.3 Hz）。
**本次审计认为该结论可能建立在一个有缺陷的实验之上** ——
如果当时的实验用了 `zero_shot_spk_id`，那么指令从一开始就被丢弃了，
测到的"变化"只是 prompt 变长带来的采样噪声。

**建议**：修复后用同样方法重跑一遍 D1 实验，重新下结论。

---

## 十、附：关键文件与代码位置

| 文件 | 行 | 内容 |
|---|---|---|
| `zjy14/custom_voice/inference/cosyvoice_engine.py` | 64 | `PROMPT_PREFIX`（含 endofprompt） |
| 同上 | 635-659 | `_resolve_expression`（emotion 仅元数据） |
| 同上 | 692 | **非流式 `inference_zero_shot` 调用点** |
| 同上 | 830 | **流式 `inference_zero_shot` 调用点** |
| `zjy14/custom_voice/schemas/synthesis.py` | 28-30 | "emotion 仅作元数据"的声明 |
| `zjy14/custom_voice/expression/control.py` | 10 | 同上 |
| `zjy14/custom_voice/expression/instruction.py` | 全文 | 指令 → speed/volume/pause 的规则解析器 |
| `zjy13/server/tts/client.py` | 294-302 | 请求体（无 emotion） |
| `zjy13/server/tts/client.py` | 427-435 | 同上（流式） |
| `zjy13/server/characters/elysia.yaml` | 25-33 | 情绪描述（只进 LLM 提示词） |
| `~/CosyVoice/cosyvoice/cli/cosyvoice.py` | 91 | `inference_zero_shot` 定义 |
| 同上 | 177 | `inference_instruct2` 定义 |
| `~/CosyVoice/cosyvoice/cli/frontend.py` | — | **`frontend_instruct2` 丢弃指令的缺陷所在** |
| `~/CosyVoice/cosyvoice/llm/llm.py` | 479 | `endofprompt` 硬断言 |
| `~/models/Fun-CosyVoice3-0.5B-2512/cosyvoice3.yaml` | 1-10 | 固定种子 1986 |
| `zjy14/docs/13_D1_EMOTION_VERDICT.md` | — | 旧结论（建议重验） |

---

## 十一、审计过程声明

本次审计**未修改任何项目文件**（审计脚本为新增临时文件，位于 `zjy14/_audit_*.py`，
输出在 `_audit_out*/`，可随时删除）。

所有结论均基于：

- 代码逐行追踪（含上游 CosyVoice 库）
- 5 组对照实验，共 30+ 次真实合成
- 客观量化：时长、基频（自相关法）、能量 RMS、过零率、md5

**未做**：主观听测（需要人工）。
