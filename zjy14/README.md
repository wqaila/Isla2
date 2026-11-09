# zjy14 · Custom Voice Engine

> CosyVoice3 角色音色与情绪表达控制引擎。
> 目标硬件：RTX 5070 12GB（WSL2）。

**当前阶段：Phase 3 完成（角色与声音管理）**

---

## 这是什么

zjy14 是 [zjy13](../zjy13/) 的**引擎升级**（决策 D2）：复用其已验证的 ESP32 固件、WebSocket 协议和角色卡，只替换语音合成引擎。

核心要解决的问题：

1. **情绪表达** —— 官方 `inference_instruct2` 在带缓存音色时**会静默丢弃指令**，且音频拉长 3.5 倍。zjy14 用混合调用模式绕过（见 [docs/02_IMPLEMENTATION_PLAN.md](docs/02_IMPLEMENTATION_PLAN.md) 第三节）
2. **音色一致性** —— 跨文本、跨情绪、跨会话保持可辨识的声音身份
3. **12GB 显存优化** —— 实测基线，不做未验证的假设

---

## 快速开始

全部命令在 **WSL** 中执行（模型与 torch 都在 WSL）。

```bash
# 启动服务
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/run_server.sh

# 另一个终端验证
curl http://127.0.0.1:50001/health
curl http://127.0.0.1:50001/api/v1/system/gpu
```

API 文档：<http://127.0.0.1:50001/docs>

### 测试

```bash
# 单元测试（145 个）
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/run_tests.sh

# Phase 1 端到端验收（需服务已启动）
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/verify_phase1.sh

# Phase 3 端到端验收（需服务已启动）
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/verify_phase3.sh

# 硬性约束验证（安全守门 / 配置登记 / 分层）
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/verify_constraints.sh

# 从 zjy13 迁移现有角色与声音（幂等）
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && source ~/cosyvoice-venv/bin/activate && PYTHONPATH=. python scripts/migrate_elysia.py"
```

---

## 接口

### `GET /health`

模型未就绪返回 **503**，绝不假报 200。

```json
{
  "ok": true,
  "engine": "stub",
  "sample_rate": 24000,
  "device": "cuda",
  "voices": [{"voice_id": "elysia", "is_builtin": false}]
}
```

### `POST /synthesize`

与 zjy13 的 `TTSClient` **完全兼容**（决策 D2 硬约束）。

请求：
```json
{"text": "你好", "voice_id": "elysia", "speed": 1.0}
```

响应：WAV 二进制 + 6 个响应头

| 头 | 说明 |
|---|---|
| `x-sample-rate` | 采样率（24000） |
| `x-duration` | 音频时长（秒） |
| `x-total-ms` | 合成耗时（毫秒） |
| `x-rtf` | 实时率 |
| `x-engine` | 引擎标识 |
| `x-voice-id` | 音色标识 |
| `x-request-id` | 请求追踪 ID |

状态码：`200` 成功 / `400` 参数错误 / `422` 请求体校验失败 / `503` 引擎未就绪 / `507` 显存不足

### `GET /api/v1/system/status` · `GET /api/v1/system/gpu`

服务状态与 GPU 实时信息。

### 角色与声音管理

```text
GET    /api/v1/characters          列出角色
POST   /api/v1/characters          创建角色（201）
GET    /api/v1/characters/{id}     取单个
PATCH  /api/v1/characters/{id}     局部更新
DELETE /api/v1/characters/{id}     删除（204）

GET    /api/v1/speakers            列出声音
POST   /api/v1/speakers            导入声音（含质量检测）
GET    /api/v1/speakers/{id}       取单个
PATCH  /api/v1/speakers/{id}       局部更新（改音频/文本会重算缓存 key）
DELETE /api/v1/speakers/{id}       删除（被角色引用时拒绝）
POST   /api/v1/speakers/{id}/validate   重新做质量检测
```

**导入声音时的质量检测**（不合格直接拒绝，并说明原因）：

| 检测 | 拒绝条件 |
|---|---|
| 时长 | < 3 秒 |
| 削波 | > 1% 样本 |
| 静音 | > 60% |
| 信噪比 | < 15dB（动态范围足够时才判） |
| 音量 | RMS < -35 dBFS |

**安全校验**：路径必须绝对、不含 `..`、文件存在、扩展名在白名单内。

---

## 配置

配置文件：`configs/gpu.yaml`

每个字段都有实测依据，不是随意取值：

| 字段 | 默认 | 依据 |
|---|---|---|
| `precision` | `fp32` | 实测 fp16 更慢 7.1%、显存 +27% |
| `speaker_cache_enabled` | `true` | 实测省 16.5% 合成耗时 |
| `token_hop_len_reset` | `true` | 上游 bug：跨调用翻倍不重置，首包 1.75s→2.25s |
| `max_concurrent_requests` | `1` | 先单并发稳定 |
| `host` | `127.0.0.1` | 默认仅回环 |

环境变量覆盖：`ZJY14_<大写字段名>`，如 `ZJY14_PORT=50002`

**配置消费登记机制**：每个字段必须在 `custom_voice/config/schema.py` 的 `CONSUMED_FIELDS` 中登记消费方，否则启动报错。这是为了兑现"不能留下配置看似生效但实际未使用的参数"。

---

## 目录结构

```text
zjy14/
├─ custom_voice/
│  ├─ config/      # 配置模型与加载（含消费登记校验）
│  ├─ core/        # 异常、结构化日志、路径解析
│  ├─ db/          # SQLite 表结构与迁移
│  ├─ audio/       # 音频读写、重采样、PCM 转换
│  ├─ speaker/     # 声音档案、质量检测、版本化缓存
│  ├─ character/   # 角色档案仓储
│  ├─ inference/   # 引擎抽象 + Stub 实现 + GPU 探测
│  ├─ schemas/     # 请求/响应模型
│  └─ server/      # FastAPI 应用与路由
├─ configs/        # gpu.yaml
├─ tests/          # 145 个测试
├─ scripts/        # 启动、测试、验收、迁移脚本
├─ docs/           # 方案、决策、阶段报告
└─ probe/          # Phase 0 环境探测脚本（可复现）
```

### 分层铁律

```text
server/routes/  →  schemas/ + inference/engine.py（协议）
                ✗  绝不 import cosyvoice / torch
```

由 `tests/test_architecture.py` 用 AST 静态检查强制，不靠自觉。

---

## 数据位置

| 内容 | 路径 | 说明 |
|---|---|---|
| 代码 | `D:\xiangmudaima\zjy14\` | Windows 编辑器可直接改 |
| SQLite | `~/zjy14-data/custom_voice.db` | **WSL 本地**，不放 /mnt/d（跨系统 IO 慢，并发写有风险） |
| 日志 | `~/zjy14-data/logs/zjy14.log` | JSON Lines，按天轮转 |
| 音频输出 | `~/zjy14-data/audio/` | |

覆盖：`ZJY14_DATA_DIR=/path/to/data`

---

## 阶段状态

| 阶段 | 状态 | 说明 |
|---|---|---|
| Phase 0 审计 | ✅ 完成 | 环境探测 + 情绪可行性验证（含重大发现） |
| Phase 1 骨架 | ✅ 完成 | 81 测试全绿，20 项端到端验收通过 |
| Phase 3 角色声音 | ✅ 完成 | 145 测试全绿，17 项端到端验收通过；elysia 已迁移 |
| **音色诊断与修复** | ⏳ **进行中** | 相似度 0.9208，音节CV +0.091；主观听感仍"不够" |
| Phase 2 表达控制 | ⬜ 待开始 | 音色收尾后启动 |
| Phase 4 GPU/流式 | ⬜ 待开始 | |
| Phase 5 ESP32 桥接 | ⬜ 待开始 | 复用 zjy13 固件 |
| Phase 6 Web 界面 | ⬜ 待开始 | React + TS 8 页 |
| Phase 7 稳定发布 | ⬜ 待开始 | |

详见 [docs/03_DECISIONS.md](docs/03_DECISIONS.md)、[docs/04_PHASE1_TASKS.md](docs/04_PHASE1_TASKS.md)、[docs/05_PHASE3_REPORT.md](docs/05_PHASE3_REPORT.md)、[docs/11_DAILY_SUMMARY_2026-11-09.md](docs/11_DAILY_SUMMARY_2026-11-09.md)。

---

## 已知限制

- **Phase 1–3 用 StubEngine**，产出正弦波而非真实语音。真实模型在 Phase 2 接入。
- **情绪表达效果未验证** —— 需要人工盲听 `_audio_probe/` 的样本。客观指标不能证明情绪被表达。
- **音色仍被认为"不够"** —— 客观相似度 0.9208（超官方基准 0.7559），但用户主观听感未达标。已定位并修复三项（静音裁剪、文本长度比、文本前端），剩余可能是模型能力上限。
- **采样随机性大** —— 同一文本重复合成，时长波动最大 46%（`top_p=0.8/top_k=25`）。评估必须多次采样取中位数。
- **仅 1 个角色素材**（elysia），无法验证"跨角色不串音色"（决策 D9）。
- 复用 `~/cosyvoice-venv`，与 CosyVoice 环境耦合；升级依赖前需跑 CosyVoice 冒烟测试。
- 声音档案只记录音频路径，不复制原始文件；外部移动文件会导致校验失败。

> 完整的成果与不足清单见 [docs/11_DAILY_SUMMARY_2026-11-09.md](docs/11_DAILY_SUMMARY_2026-11-09.md)。

---

## 相关文档

| 文档 | 内容 |
|---|---|
| [11_DAILY_SUMMARY_2026-11-09.md](docs/11_DAILY_SUMMARY_2026-11-09.md) | **当日完整总结（成果 + 不足）** |
| [02_IMPLEMENTATION_PLAN.md](docs/02_IMPLEMENTATION_PLAN.md) | 落地方案：环境审计、关键技术发现、架构、风险 |
| [03_DECISIONS.md](docs/03_DECISIONS.md) | 10 项决策记录 |
| [04_PHASE1_TASKS.md](docs/04_PHASE1_TASKS.md) | Phase 1 详细任务与验收标准 |
| [05_PHASE3_REPORT.md](docs/05_PHASE3_REPORT.md) | Phase 3 交付报告 |
| [06_TIMBRE_DIAGNOSIS.md](docs/06_TIMBRE_DIAGNOSIS.md) | 音色诊断：定位"像机器人"的根因 |
| [07_TIMBRE_FIX_PLAN.md](docs/07_TIMBRE_FIX_PLAN.md) | 音色修复方案（三个真根因） |
| [08_REFERENCE_SUFFICIENCY.md](docs/08_REFERENCE_SUFFICIENCY.md) | 参考音频是否够（相似度量化） |
| [09_FIXED_TEXT_RESULT.md](docs/09_FIXED_TEXT_RESULT.md) | 补全文本后复测 |
| [10_TEXT_FRONTEND.md](docs/10_TEXT_FRONTEND.md) | 文本前端启用（无需 token） |
| [原始提示词](Fun-CosyVoice3.0_RTX5070_12GB_完整优化开发提示词.md) | 需求来源 |
