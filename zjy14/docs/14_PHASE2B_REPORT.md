# Phase 2b：表达控制（路线乙）

> 日期：2026-11-16
> 依据：D1 实测结论（情绪指令无效，见 [13_D1_EMOTION_VERDICT.md](13_D1_EMOTION_VERDICT.md)）
> 结果：**完成**。235 个测试全绿，8 项端到端验收通过

---

## 一、核心思路

D1 证明情绪指令无效后，改走路线乙：**只控制确定生效的维度**。

| 维度 | 是否生效 | 实现方式 |
|---|---|---|
| `speed` 语速 | ✅ 实测有效（跨度 57%） | 模型参数 |
| `volume` 音量 | ✅ 有效 | 音频层增益，不依赖模型 |
| `pause_profile` 停顿 | ✅ 有效 | 分句合成 + 插入静音 |
| `emotion` 情绪 | ❌ **无效** | 仅作元数据，不传给模型 |
| `style` 风格 | ❌ 无效 | 仅作元数据 |

**关键设计**：`ExpressionParams.effective_controls()` 只返回真正生效的三项，
`metadata_only()` 返回元数据。这个分离是显式的、可测试的，避免以后有人误以为情绪生效了。

---

## 二、前置验证（先测再做）

在写代码前先量化了每个维度的真实效果，避免凭假设设计。

### 2.1 speed 实测（每组 3 次采样取中位数）

| speed | 时长 | 语速 | 相对 1.0 |
|---|---|---|---|
| 0.8 | 7.64s | 4.35 字/秒 | +20.1% |
| 0.9 | 6.04s | 5.43 字/秒 | −5.0% |
| 1.0 | 6.36s | 5.52 字/秒 | 基准 |
| 1.1 | 5.22s | 6.42 字/秒 | −17.9% |
| 1.2 | 4.86s | 6.70 字/秒 | −23.6% |

**时长跨度 57%，可用。但 F0 跨度 26.2Hz** —— 说明极端语速会让音色轻微漂移。
因此设定安全区间 `[0.85, 1.15]`，超出时记录警告但不阻止。

### 2.2 标点对停顿的影响（关键发现）

| 写法 | 时长 | 静音占比 | 停顿数 |
|---|---|---|---|
| 无标点 | 3.20s | 10.3% | 1 |
| 逗号 | 4.00s | 13.8% | 2 |
| 句号分隔 | 4.24s | 11.7% | 2 |
| 长停顿（双句号） | 3.48s | 15.9% | 1 |
| 换行 | 3.92s | 16.4% | 1 |
| 省略号 | 3.92s | 18.0% | 2 |

**标点影响很小**：静音占比只在 10.3%~18.0% 之间，模型不会因为多写一个句号就多停 500ms。

**结论：停顿必须靠分句拼接实现，不能靠标点。**

### 2.3 分句拼接效果

| 方式 | 时长 | 静音占比 |
|---|---|---|
| 整段合成 | 5.76s | 10.7% |
| 分句拼接（250ms 间隔） | 6.94s | **29.5%** |

静音占比从 10.7% 提到 29.5%，效果明确。

---

## 三、交付内容

### 3.1 新增模块

| 文件 | 职责 |
|---|---|
| `expression/control.py` | 结构化参数、区间校验、生效/元数据分离 |
| `expression/instruction.py` | 自然语言指令解析（规则匹配，不依赖 LLM） |
| `expression/pause.py` | 文本分句、静音拼接、交叉淡化、音量增益 |

### 3.2 停顿档位

| 档位 | 段间静音 | 用途 |
|---|---|---|
| `none` | 0（不分句） | 整段合成，最快 |
| `tight` | 80ms | 紧凑、急切 |
| `natural` | 180ms | 自然（默认） |
| `relaxed` | 350ms | 舒缓、留白 |
| `dramatic` | 600ms | 戏剧化、沉重 |

### 3.3 指令解析器

规则匹配，中文优先，兼顾英文。示例：

| 输入 | 解析结果 |
|---|---|
| 「放慢速度」 | speed 0.85 |
| 「快一点」 | speed 1.12 |
| 「大声一点」 | volume 1.25 |
| 「多停顿一些」 | pause relaxed |
| 「像在安慰人」 | emotion comforting（仅元数据） |
| 「温柔一点，放慢速度」 | emotion comforting + speed 0.88 |
| 「用火星语说话」 | 全部未识别，记录警告 |

**设计原则**：
- 按关键词长度降序匹配（「慢一点」优先于「慢」）
- 未识别内容记录到 `unmatched` 并生成警告，不静默忽略
- 情绪词只填元数据，不进 `effective_controls()`

### 3.4 请求模型变更

`SynthesisRequest` 新增三个生效字段，并把原有情绪字段标注为元数据：

```python
# 真正生效
volume: float = 1.0            # 音频层增益
pause_profile: str = "natural" # 停顿档位
instruction: str | None        # 自然语言指令

# 仅元数据（D1 实测无效）
emotion: str | None            # 字段说明里明确标注"仅记录，不影响合成"
intensity: float | None
style: str | None
```

**向后兼容**：zjy13 的 `TTSClient` 只传 text/voice_id/speed，不受影响。

---

## 四、发现并修复的两个真 bug

### 4.1 上游 wetext 下载拖慢启动 90 秒（重要）

**现象**：Phase 2b 验收脚本等待服务就绪 90 秒超时，日志显示：

```
Downloading [hi/tn/verbalizer.fst]: 100%|██████████| 13.1M/13.1M [00:57<00:00, 240kB/s]
Downloading [hi_en/itn/tagger.fst]: ... 403 Forbidden (重试 5 次)
```

**根因**：
`CosyVoice3.__init__` → `CosyVoiceFrontEnd.__init__` 会 `import wetext`，
而 wetext 的 `__init__` 无条件调 `snapshot_download()`。
无 token 时它会**先下载部分文件**（耗时 57 秒），再在后续文件上 403 失败。

我的 `enable_wetext_frontend` 补丁在构造**之后**才调用，救不了这次下载。

**修复**：在构造前 monkey-patch `modelscope.snapshot_download` 让它立刻抛异常，
构造完再恢复。

```python
_orig = _block_modelscope_download()
try:
    self._model = CosyVoice3(...)
finally:
    _restore_modelscope_download(_orig)
```

**效果：启动时间从 90s+ 降到 19s。**

### 4.2 tanh 软限幅公式错误

原实现：

```python
out = np.tanh(out * 1.2) / np.tanh(1.2) * 0.99
```

当输入接近 1.0 时，`tanh(1.2)/tanh(1.2)*0.99 = 0.99` 看起来没问题，
但乘上 1.2 后的输入会让中间值超出，实测输出达到 **1.098**——超过 1.0 会削波。

**修复**：改用 `tanh(x * 1.5) / tanh(1.5)` 再 `clip(-0.99, 0.99)`，严格保证不超界。

被测试 `test_apply_gain_no_clipping` 抓到。

---

## 五、验收结果

### 5.1 端到端（8 项全通过）

| 项 | 结果 |
|---|---|
| speed 生效 | 7.04s (0.85) > 5.82s (1.0) > 5.36s (1.15) ✅ |
| volume 生效 | RMS 3219 → 6360（比值 1.98）✅ |
| pause 生效 | 静音占比 27.2% (none) → 35.8% (dramatic) ✅ |
| 指令解析生效 | 「放慢速度」8.58s vs 「快一点」5.90s ✅ |
| emotion 仅元数据 | 传与不传差异 5.3%（在采样噪声内）✅ |
| 未识别指令 | 返回 200 + 警告，不报错 ✅ |
| 越界语速 | 记录「超出建议区间」警告 ✅ |
| /health | 正常，暴露 text_frontend ✅ |

### 5.2 测试

```
235 个测试全绿（Phase 2a 的 186 + 本阶段 49）
零警告
```

新增 `tests/test_expression.py` 覆盖：
- 参数区间校验与归一化（15 个）
- 指令解析（11 个，含中英文、组合、未识别）
- 参数合并优先级（3 个）
- 文本分句（7 个，含超长切分、碎片合并）
- 音频处理（8 个，含削波保护、交叉淡化）
- 引擎集成（3 个，验证 emotion 不进生效参数）

---

## 六、当前能力矩阵

| 能力 | 状态 | 备注 |
|---|---|---|
| 音色克隆 | ✅ | 相似度 0.9208（超官方基准 0.7559） |
| 语速控制 | ✅ | 0.5–2.0，安全区间 0.85–1.15 |
| 音量控制 | ✅ | 0.5–1.5，带软限幅 |
| 停顿控制 | ✅ | 5 档，靠分句拼接 |
| 自然语言指令 | ✅ | 规则匹配，中英文 |
| 文本正则化 | ✅ | wetext 本地 fst |
| **情绪表达** | ❌ | **模型不支持**（D1 实测） |
| 流式 | ⬜ | Phase 4 |
| 并发 | ⬜ | Phase 4 |

---

## 七、使用示例

```bash
# 语速 + 音量
curl -X POST localhost:50001/synthesize \
  -H 'Content-Type: application/json' \
  -d '{"text":"今天天气真不错","speed":0.9,"volume":1.2}'

# 停顿档位
curl -X POST localhost:50001/synthesize \
  -H 'Content-Type: application/json' \
  -d '{"text":"第一句。第二句。第三句。","pause_profile":"dramatic"}'

# 自然语言指令
curl -X POST localhost:50001/synthesize \
  -H 'Content-Type: application/json' \
  -d '{"text":"今天天气真不错","instruction":"温柔一点，放慢速度，多停顿"}'

# 情绪标签（仅记录，不影响音频）
curl -X POST localhost:50001/synthesize \
  -H 'Content-Type: application/json' \
  -d '{"text":"今天天气真不错","emotion":"happy","intensity":0.8}'
```

---

## 八、仍未解决

| 项 | 说明 |
|---|---|
| 情绪表达 | 模型能力上限，非工程问题。需换引擎（如 GPT-SoVITS）才可能解决 |
| 流式 | Phase 4 |
| 并发 | Phase 4，配置项已存在但无调度器 |
| 多音色 | 只有 elysia |
| 分句的音色稳定性 | 分句过多会让每段文本长度比低于 0.5，可能影响音色 |

---

## 九、复现命令

```bash
# 前置能力验证
wsl -e bash probe/run_d1.sh probe/_p2b_capability.py
wsl -e bash probe/run_d1.sh probe/_p2b_speed.py

# 离线补丁验证
wsl -e bash probe/run_d1.sh probe/_p2b_offline.py

# 测试
wsl -e bash scripts/run_tests.sh

# 端到端验收
wsl -e bash scripts/verify_phase2b.sh
```
