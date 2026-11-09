# Phase 3：角色与声音管理

> 状态：**已完成**（2026-11-09）
> 估算：3–4 人日 · 实际交付：145 个测试 + 17 项端到端验收

---

## 一、本阶段目标

把"角色"和"声音"从 zjy13 的硬编码 YAML 变成可管理的数据资产：

| 目标 | 交付 |
|---|---|
| 角色 CRUD | SQLite 持久化 + REST API |
| 声音档案管理 | 元数据 + 质量报告 + 版本 |
| 参考音频质量检测 | 7 项检测，blocking/warning 分级 |
| 版本化缓存 | 内容哈希 key，换素材/模型自动失效 |
| 迁移现有资产 | elysia 从 zjy13 导入 |

---

## 二、关键设计决策

### 2.1 为什么用内容哈希而不是 mtime 做缓存 key

缓存 key = `sha256(speaker_id | 音频内容哈希 | 模型版本 | 预处理版本 | 参考文本)`

用文件 mtime 的问题：
- 用户可能覆盖同名文件但内容不变 → mtime 变了，缓存白失效
- 用户可能改回旧内容 → mtime 新，但缓存其实仍然有效

**更关键的是把 `prompt_text` 纳入 key**。零样本克隆要求参考音频与文本严格对应，只改文本不改音频同样会让合成跑飞——这一点在 Phase 0 的探针里已经实证过（前缀错位导致卷积报错）。

### 2.2 为什么质量检测要分 blocking / warning

原提示词要求"参考音频格式检查、静音检测、削波检测、时长检查"。

但简单返回"合格/不合格"没有价值——用户不知道为什么不合格、该怎么改。所以：

- **blocking**：确实无法用（太短、严重削波、大量静音、音量过低、低信噪比）→ 拒绝导入
- **warning**：能用但效果会打折（偏长、轻微削波、静音偏多、中等信噪比、多人说话嫌疑）→ 允许但提示
- **info**：只是告知会做转换（重采样、降混、SNR 无法估计）

每条都带**具体数值 + 可操作建议**，而不只是错误码。

### 2.3 SNR 检测的坑（实测发现）

第一版用"10% 分位帧能量"作噪声底。测试用纯正弦波时，**SNR 被算成 0.1dB**，判成低信噪比直接拒绝——这是误判。

原因：连续稳态音频（纯音、持续长音、已做压缩的素材）**没有安静帧**，10% 分位≈信号本身，减出来就是 0dB。

修法：先算动态范围（P95 - P10），低于 6dB 说明音频里根本没有安静片段，此时**跳过 SNR 检测**并标注 `snr_not_estimable`。

这条修正是真实素材处理的经验，纯音测试暴露出来的。

### 2.4 为什么改参考文本也要让缓存失效

`make_cache_key` 把 `prompt_text` 纳入哈希。测试 `test_cache_key_changes_with_transcript` 专门覆盖。

同理，`PATCH /speakers/{id}` 只改 `display_name` 时**不重算** key（`test_update_speaker_display_name_keeps_key`），避免无意义的缓存失效。

### 2.5 删除保护

- 声音被角色引用时**拒绝删除**，提示哪些角色在用
- 删除角色**不级联删除声音**（声音可能被多角色共用）

这两条都是防止"角色静默失去音色"这种难排查的问题。

---

## 三、交付内容

### 新增模块

| 文件 | 职责 |
|---|---|
| `audio/convert.py` | 读写 WAV、重采样、PCM 转换、峰值归一化 |
| `speaker/validate.py` | 参考音频质量检测（7 项） |
| `speaker/registry.py` | 声音档案 + 参考音频元数据仓储 |
| `speaker/cache.py` | 版本化 LRU 缓存 |
| `character/repository.py` | 角色档案仓储 |
| `schemas/character.py` | 请求/响应模型 |
| `server/routes/characters.py` | 角色 API（5 端点） |
| `server/routes/speakers.py` | 声音 API（6 端点） |
| `scripts/migrate_elysia.py` | 从 zjy13 迁移现有资产 |

### API 端点

```text
GET    /api/v1/characters
POST   /api/v1/characters
GET    /api/v1/characters/{id}
PATCH  /api/v1/characters/{id}
DELETE /api/v1/characters/{id}

GET    /api/v1/speakers
POST   /api/v1/speakers
GET    /api/v1/speakers/{id}
PATCH  /api/v1/speakers/{id}
DELETE /api/v1/speakers/{id}
POST   /api/v1/speakers/{id}/validate
```

### 质量检测项

| 检测 | blocking 条件 | warning 条件 |
|---|---|---|
| 时长 | < 3s | > 15s |
| 削波 | > 1% 样本 | 有削波但 < 1% |
| 静音 | > 60% | > 30% |
| 信噪比 | < 15dB（动态范围 ≥6dB 时才判） | < 25dB |
| 音量 | RMS < -35 dBFS | RMS > -6 dBFS |
| 声道 | — | > 1 声道 |
| 多人说话嫌疑 | — | 有声/静音切换 > 2 次/秒 |

评分权重：信噪比 0.35 + 时长 0.25 + 削波 0.20 + 静音 0.10 + 电平 0.10

---

## 四、迁移结果

```
[检测] 可用（评分 0.93）
[创建] 声音档案 elysia  score=0.9307  key=639c511d
[创建] 角色 elysia  绑定声音 elysia

=== 当前数据 ===
  声音 elysia       9.7s  You are a helpful assistant.<|...
  角色 elysia       爱莉希雅  声音=elysia  情绪=['neutral', 'happy', 'sad', 'angry']
```

**只读 zjy13 素材，未修改**。脚本幂等，重复执行会跳过已存在的记录。

迁移时做的字段映射：
- `identity` → `description`
- `personality_traits` + `values` + `emotional_baseline` → `personality`
- `speech_style` → `speaking_habits`
- `allowed_emotions` → 仅 3 条官方情绪（决策 D4，comforting/playful 待 Phase 2 验证）

**注意**：zjy13 角色卡里的 `emotional_expression` 是给 LLM 用的自然语言，**不是**情绪控制参数，没有映射到 `voice_instruction_template`。避免把"文本描述"误当成"模型指令"。

---

## 五、测试

145 个测试（Phase 1 的 81 个 + 本阶段 64 个）：

| 文件 | 覆盖 |
|---|---|
| `test_speaker_quality.py` | 音频转换、质量检测各分支、SNR 边界 |
| `test_character_speaker.py` | 仓储 CRUD、外键级联、缓存 LRU/失效 |
| `test_api_character.py` | 26 个 API 行为，含安全与错误语义 |

端到端 17 项验收（`scripts/verify_phase3.sh`），全部通过。

### 测试抓到的真实 bug

1. **204 状态码不能有响应体** —— FastAPI 在注册路由时就断言失败，导致整个应用无法导入。修法：显式返回 `Response(status_code=204)`。
2. **SNR 对稳态音频误判** —— 见 2.3。
3. **Pydantic `model_` 保护命名空间** —— `model_id` / `model_version` 与 pydantic 内置命名冲突，需 `protected_namespaces=()`。共 4 个类受影响。

---

## 六、验收清单

```text
[x] pytest 145 个全绿，零警告
[x] elysia 角色与声音已迁移，可读
[x] 角色 CRUD 全流程可用
[x] 局部更新不清空未传字段
[x] 非法情绪标签 -> 422
[x] 非法 character_id（含 /）-> 422
[x] 绑定不存在的音色 -> 400
[x] 路径穿越 -> 400
[x] 不存在的音频文件 -> 400
[x] 空参考文本 -> 422
[x] 低质量音频（1s）-> 400，提示 too_short
[x] 被引用的声音不允许删除
[x] 重新质量检测可用
[x] 改参考文本 -> 缓存 key 变化
[x] 删除角色 -> 204，再查 404
```

---

## 七、已知限制

| 限制 | 说明 |
|---|---|
| 仅 1 个角色素材 | 无法验证"跨角色不串音色"（决策 D9） |
| 多人说话检测是粗筛 | 基于能量包络切换频率，只能提示不能确定 |
| 音频格式有限 | 依赖 soundfile，支持 wav/flac/mp3/m4a/ogg；不支持需要 ffmpeg 的格式 |
| 未做音频文件复制 | 声音档案只记录路径，不复制原始文件到 data/ 目录 |
| 未实现角色试听 | 需要 Phase 2 的真引擎才能合成 |

---

## 八、下一阶段

Phase 3 完成，Phase 2（表达控制）仍等 **D1 盲听结论**。

可继续的选项：
- **Phase 4（GPU 与流式优化）** —— 部分可做，但流式需要真引擎
- **Phase 6（Web 界面）** —— 可以先搭前端骨架对接现有 API
- **Phase 0 收尾** —— 补 `00_REPOSITORY_AUDIT.md` 与 `01_BASELINE_REPORT.md`
