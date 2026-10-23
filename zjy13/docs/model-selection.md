# DeepSeek 模型选型报告

> 所有数据均为 2026-10-23 在 **D:\xiangmudaima\zjy13** 本机真实调用 `https://api.deepseek.com` 的实测结果。
> 网络为普通家庭宽带，未做链路优化。测试用同一份默认角色卡「小满」。

## 1. 可用模型（来自 `/models` 真实返回）

| id | 名称 | 上下文窗口 | 最大输出 | 输入模态 | effort 档位 |
| --- | --- | --- | --- | --- | --- |
| `deepseek-flash` | DeepSeek-V4.1-Flash | 1,048,576 | 393,216 | 文本 + **图片** | low / high(默认) / max |
| `deepseek-v4-pro` | DeepSeek-V4-Pro | 1,048,576 | 393,216 | 仅文本 | low / high(默认) / max |

两个都是**推理模型**：响应中带 `reasoning_content`，`usage` 中有 `reasoning_tokens`。默认开启思考。

## 2. 关键发现：思考开关对首字延迟影响巨大

DeepSeek V4 系列默认先"想"再"说"。语音交互场景下，思考阶段完全是等待。

实测 `thinking={"type":"disabled"}` 可完全关闭思考（`reasoning_tokens` 变为不存在）：

| 模型 | thinking | 首字延迟 mean | 首字延迟 P95 | 总耗时 mean |
| --- | --- | --- | --- | --- |
| deepseek-flash | 默认（开） | 1724 ms | 2564 ms | 2233 ms |
| deepseek-flash | **disabled** | **723 ms** | **945 ms** | 1982 ms |
| deepseek-v4-pro | 默认（开） | 3413 ms | 4918 ms | 4101 ms |
| deepseek-v4-pro | **disabled** | **873 ms** | **999 ms** | 2969 ms |

> 关掉思考，flash 首字快 **约 1 秒**，pro 快 **约 2.5 秒**。

该开关已接入本项目（`LLM_THINKING`），`.env` 当前为 `disabled`。

## 3. 完整链路实测（经本项目客户端 + 默认角色卡，thinking=disabled，各 15 次）

| 指标 | deepseek-flash | deepseek-v4-pro |
| --- | --- | --- |
| 首字延迟 mean | **646 ms** | 877 ms |
| 首字延迟 P95 | **984 ms** | 1153 ms |
| 总耗时 mean | **996 ms** | 1202 ms |
| 总耗时 P95 | **1274 ms** | 1595 ms |
| 输出字符 mean | 55 | 59 |
| 完成 token 合计（15 次） | 480 | 309 |

> pro 输出更短（更精炼），总 token 更少；但首字和总耗时都高于 flash。

## 4. 角色一致性观察

两个模型都能稳定遵守角色卡的「不编造」约束。面对"帮我编一个昨天部门会议记录"：

- **flash**：`这个我不能帮你编。会议记录得是真的才行，我瞎写的内容你拿去用会出事。`
- **pro**：`这个不行。我不编造没发生过的事。如果你需要会议记录，我可以帮你整理要点或搭个模板。`

面对"明天会下雨吗"，两者都明确表示拿不到实时天气，没有编造。**这一点符合文档要求（不得为符合角色而编造事实）。**

风格差异：
- flash 更口语、更愿意多聊一句（"你是身体累，还是脑子累？"），情绪感稍强。
- pro 更克制、更短，像"精简版"，但少了一点陪伴感。

## 5. 结论与建议

### 已定稿：`deepseek-flash` + `LLM_THINKING=disabled`

**决策（2026-10-23，用户确认）**：接受 `deepseek-flash`，不做双模型路由。

理由：

1. **首字延迟低约 230ms（mean）/ 170ms（P95）**，语音交互对首字最敏感。
2. 总耗时也更低，端到端更快。
3. 角色表现更自然、更愿意互动，符合"陪伴型角色"的定位。
4. 1M 上下文对第一版绰绰有余。

### 明确不做：双模型路由

用户已确认不做"复杂问题走 pro"的路由。理由：第一版以功能闭环和响应速度为先，
路由会增加状态机复杂度和延迟不确定性。若后续需要，可在 `ChatAdapter` 层扩展，
当前架构已支持（换模型只需改 `LLM_MODEL`）。

### 最终参数（已写入 `.env`）

| 参数 | 值 | 依据 |
| --- | --- | --- |
| `LLM_MODEL` | `deepseek-flash` | 首字 P95 984ms，优于 pro 的 1153ms |
| `LLM_THINKING` | `disabled` | 关闭思考，首字快约 1 秒 |
| `LLM_REQUEST_TIMEOUT_S` | 20 | 实测 P95 总耗时 1.3s，20s 覆盖网络抖动 |
| `LLM_TOTAL_TIMEOUT_S` | 45 | 语音场景超过 45s 已无交互意义 |
| `LLM_MAX_ATTEMPTS` | 3 | 含首次；退避 0.5s→1.0s，累计约 1.5s |
| `LLM_BACKOFF_MAX_S` | 4 | 退避上限，避免长等待 |
| `LLM_MAX_TOKENS` | 256 | 约 170 汉字，够角色卡要求的 1~5 句，同时拦住长篇 |
| `LLM_TEMPERATURE` | 0.7 | 保持角色表达自然度 |

## 6. 成本细节：prompt 缓存实际生效

角色卡渲染后的系统提示词约 **450 token**，每轮都会重发。但实测发现 DeepSeek 的
**prompt 缓存确实命中**：

```
prompt_tokens: 458
prompt_cache_hit_tokens: 256
prompt_cache_miss_tokens: 202
```

同一角色卡连续请求时，稳定命中的部分约 256 token，且**首字延迟降到 417ms**
（未命中时约 646ms）。这意味着：

- 固定系统提示词越长，缓存收益越明显，不必为了省 token 而压缩角色卡。
- 命中率依赖请求前缀完全一致，因此 `build_messages` 必须保证系统提示词内容稳定，
  不要在 system 里拼接时间戳、随机数等易变内容——当前实现已满足。

仍建议保留 `max_history_messages` 限制（默认 20 条），避免历史无限增长。

## 7. 复现命令

```powershell
cd D:\xiangmudaima\zjy13
$env:PYTHONUTF8="1"

# 单轮冒烟
.\.venv\Scripts\python.exe -m tools.llm_smoke "今天有点累，不想动了。"

# 双模型对比（会真实计费）
.\.venv\Scripts\python.exe -m tools.bench_models --rounds 3
```

结果 JSON 保存在 `logs/bench_*.json`。

## 8. 未验证项

- 多轮长对话下缓存命中率是否会随历史增长而下降（仅验证了单轮重复请求）。
- 并发限流阈值（未测，当前单用户串行场景不涉及）。
- 图片输入能力（flash 支持，但本项目不需要）。
- 真实 ESP32 链路下的端到端延迟（需等固件阶段）。
