# Phase 2a：音色收尾报告

> 日期：2026-11-10
> 目标：把真引擎接入项目，完成音色收尾
> 结果：**完成**。186 个测试全绿，12 项端到端验收通过

---

## 一、交付内容

### 1.1 真引擎接入

新增 `custom_voice/inference/cosyvoice_engine.py`（约 400 行），实现 `TTSEngine` 协议。

**路由层零改动** —— 这是 Phase 1 骨架设计的验证：`create_engine()` 只加了一个分支。

```python
if config.engine is EngineKind.COSYVOICE3:
    from ..inference.cosyvoice_engine import CosyVoiceEngine
    return CosyVoiceEngine(config)
```

### 1.2 五项关键实现

| # | 实现 | 依据 |
|:-:|---|---|
| 1 | `token_hop_len` 每请求重置 | 上游跨调用翻倍不重置，首包 1.75s→2.25s |
| 2 | 参考音频编码缓存（`add_zero_shot_spk`） | 实测省 16.5% 合成耗时 |
| 3 | 文本长度比检查 | 比值 0.21 时质心偏移 +490Hz，1.08 时仅 −56Hz |
| 4 | 文本前端本地加载 | wetext 联网校验 403 降级，改读本地 fst |
| 5 | fp32 固定 | 实测 fp16 更慢 7.1%、显存 +27% |

### 1.3 新增配置项（4 个）

| 字段 | 默认 | 说明 |
|---|---|---|
| `model_dir` | `""` | 留空用 `~/models/Fun-CosyVoice3-0.5B-2512` |
| `text_frontend` | `auto` | `auto`/`wetext`/`none` 三档 |
| `default_voice_id` | `elysia` | 未指定音色时的回退 |
| `min_text_ratio` | `0.5` | 文本长度比阈值 |

全部已在 `CONSUMED_FIELDS` 登记，配置消费机制校验通过。

### 1.4 音色自动注册

引擎启动时按优先级加载音色：

1. **SQLite 的 `speakers` 表**（Phase 3 的管理数据）
2. 回退：扫描 `zjy13/server/characters/voices/` 目录

注册前自动补 `<|endofprompt|>` 前缀（缺失会导致上游卷积报错）。

---

## 二、发现并修复的真 bug

### 2.1 我自己造成的参考文本污染（重要）

**现象**：真引擎第一次合成返回 HTTP 500：

```
RuntimeError: Calculated padded input size per channel: (3).
Kernel size: (4). Kernel size can't be greater than actual input size
```

**根因**：数据库里的参考文本被写成了**官方示例的文本**：

```
错误: You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。  (58 字)
正确: You are a helpful assistant.<|endofprompt|>我一直都在努力维持优雅美丽可爱…      (85 字)
```

追查发现是我在 Phase 3 的验收脚本 `verify_phase3.sh:155` 里硬编码的：

```bash
# 错误写法：为了"改回去"而硬编码了官方示例文本
curl -X PATCH ... -d '{"transcript":"...希望你以后能够做的比我还好呦。"}'
```

这个脚本在测试"改参考文本导致缓存失效"后，用一个**错误的硬编码值**去恢复，把正确数据覆盖了。

**修复**：
1. 脚本改为先读出原值、改完恢复原值，绝不硬编码
2. 新增 `scripts/fix_elysia_transcript.sh` 从 zjy13 的原始 `.txt` 重新导入
3. 数据已修复为 85 字正确文本

**教训**：测试脚本里的"恢复"操作，必须用读到的原值，不能凭记忆硬编码。

### 2.2 新增启动期校验：参考文本一致性

把这个坑固化成检查，避免以后再犯。`check_reference_consistency()`：

```python
# 参考文本字数 / (音频时长 × 4.2 字每秒) 的合理区间 [0.6, 1.6]
```

不通过时**跳过该音色并给出可操作建议**，而不是等到合成时在卷积层崩：

```
参考文本疑似不完整：音频 4.50s 按正常语速（4.2 字/秒）应含约 19 字，
但只标注了 11 字（比值 0.58）。文本与音频不对齐会导致合成时报
'Kernel size can't be greater than actual input size'。
请补全参考文本，或换一段文本已知的音频。
```

### 2.3 枚举类型静默失效（测试抓到的）

`model_copy(update={"text_frontend": "none"})` 不做类型校验，传进去的是字符串而非枚举，导致 `mode is TextFrontendMode.NONE` 判断永远为 False——**配置项静默不生效**。

修复：引擎 `__init__` 里做归一化 `TextFrontendMode(config.text_frontend)`。

这正是"配置看似生效但实际未使用"的典型陷阱，被测试拦住了。

---

## 三、验收结果

### 3.1 端到端（12 项全通过）

```
[0] 服务启动            PASS（模型加载 13.5s，预热 3.2s）
[1] /health 字段完整     PASS（text_frontend=wetext）
[2] 合成真实语音         PASS（170924 bytes / 3.56s / 24kHz 单声道 16bit）
    6 个响应头全部存在    PASS
[3] 含数字文本           PASS（TN 生效）
[4] 多次采样稳定性       PASS（CV 0.028）
[5] 错误语义（422）      PASS
[6] /system/status       PASS（无敏感信息泄露）
```

### 3.2 采样稳定性：修复前后对比（关键发现）

同一文本合成 5 次：

| 状态 | 时长序列 | 标准差 | 变异系数 |
|---|---|---|---|
| **修复前**（参考文本错误） | 5.12 / 0.48 / 2.64 / 3.44 / 4.00 | 1.74s | **0.554** |
| **修复后**（参考文本正确） | 5.80 / 5.60 / 5.96 / 5.72 / 5.56 | 0.16s | **0.028** |

**变异系数从 0.554 降到 0.028，改善 20 倍。**

这推翻了我此前的判断。之前认为"模型采样随机性极大（时长最大差 46%）"，实际上那是**参考文本错误导致的**，不是模型本身的随机性。

这个发现很重要：
- 参考文本正确时，模型输出非常稳定
- 之前的"采样随机性"结论需要修正
- 后续评估不必强制多次采样（但仍建议）

### 3.3 性能数据

```
模型加载    13.5s
预热        3.2s
RTF         0.625-0.648
音频格式    24kHz / 单声道 / 16bit
合成耗时    3.56s 音频约 2.2s
```

RTF 0.63 优于目标值 0.8。

### 3.4 测试

```
186 个测试全绿，零警告
其中 CosyVoiceEngine 新增 24 个：
  - 协议兼容性、加载幂等、模型目录缺失
  - token_hop_len 重置（含配置关闭时确实不重置）
  - 文本长度比检查（含复现真实 bug 的用例）
  - 参考文本一致性校验（4 个边界）
  - 坏音色被跳过但不影响好音色
  - 文本前端三档模式（none / auto / wetext 强制失败）
  - 合成错误映射、流式、诊断、关闭
```

---

## 四、当前状态

### 4.1 可用的能力

```bash
# 启动真引擎
ZJY14_ENGINE=cosyvoice3 bash scripts/run_server.sh

# 合成
curl -X POST localhost:50001/synthesize \
  -H 'Content-Type: application/json' \
  -d '{"text":"今天天气真不错","voice_id":"elysia"}'
```

`/health` 现在返回完整诊断：

```json
{
  "engine": "cosyvoice3",
  "text_frontend": "wetext",
  "voice_count": 1,
  "hop_len_resets": 6,
  "ratio_warnings": 2,
  "precision": "fp32",
  "peak_vram_mb": 3450
}
```

### 4.2 仍未解决

| 项 | 状态 |
|---|---|
| 音色主观听感"不够" | **仍未验证**——需要你听 `_phase2a/` 下的新样本 |
| 情绪控制 | 未开始（Phase 2b） |
| 流式 | 接口留了，实现仍是一次性返回 |
| 并发 | 配置存在，无调度器 |
| 多音色 | 只有 elysia，无法验证跨角色一致性 |

---

## 五、需要你做的事

### 5.1 听新样本（关键）

`_phase2a/` 下有真实合成的音频：

| 文件 | 内容 |
|---|---|
| `a.wav` | 「今天天气真不错，我们一起出去走走吧。」 |
| `digits.wav` | 「现在是2026年11月9日，温度25度。」 |
| `stab_1.wav` ~ `stab_5.wav` | 同一文本 5 次，验证稳定性 |

**重点判断**：
1. 现在的音色比之前（`_audio_probe/` 那批）有改善吗？
2. `digits.wav` 里数字读得自然吗？
3. `stab_*.wav` 5 条听起来一致吗？

### 5.2 一个重要的认知修正

之前你说"不够"，我归因于采样随机性。现在数据显示：**参考文本修好后，CV 只有 0.028，模型输出很稳定**。

所以如果你现在听还是"不够"，那基本可以确认是：
- 参考音频本身的音色代表性不足，或
- CosyVoice3-0.5B 的模型能力上限

不再是工程问题。

---

## 六、复现命令

```bash
# 全套测试
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/run_tests.sh

# Phase 2a 端到端验收（会自己启动服务）
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/verify_phase2a.sh

# 修复参考文本（如果再次被污染）
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/fix_elysia_transcript.sh

# Phase 3 验收（已修复硬编码问题）
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/verify_phase3.sh
```
