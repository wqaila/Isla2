# zjy14 · Custom Voice Engine

> CosyVoice3 角色音色与情绪表达控制引擎。
> 目标硬件：RTX 5070 12GB（WSL2）。

**当前阶段：设备状态监控 Web 端完成**

---

## 这是什么

zjy14 是 [zjy13](../zjy13/) 的**引擎升级**（决策 D2）：复用其已验证的 ESP32 固件、WebSocket 协议和角色卡，只替换语音合成引擎。

核心要解决的问题：

1. **情绪表达** —— 官方 `inference_instruct2` 在带缓存音色时**会静默丢弃指令**，且音频拉长 3.5 倍。zjy14 用混合调用模式绕过（见 [docs/02_IMPLEMENTATION_PLAN.md](docs/02_IMPLEMENTATION_PLAN.md) 第三节）
2. **音色一致性** —— 跨文本、跨情绪、跨会话保持可辨识的声音身份
3. **12GB 显存优化** —— 实测基线，不做未验证的假设

---

## 快速开始

### 方式一：网页一键启动（最省事，推荐）

```powershell
cd D:\xiangmudaima\zjy14
.\scripts\start_web.ps1 -OpenBrowser
```

这一步会自动准备「启动器（:50100）+ 前端 dev 服务器（Vite :5173）」，
并打开 **`http://127.0.0.1:5173/services`**；在页面上点
**「一键启动全部」** 即可拉起 **ASR + LLM + TTS + 网关**。

其中 TTS 用的是 **zjy14 自己的 CosyVoice3**（WSL，:50001），
**不是** zjy13 自带的 `tts_server_wsl`（:50000）。

也可以双击 `一键启动网页.bat`。想连语音链路一起自动启动就加 `-WithServices`。
详见 [docs/33_LAUNCHER.md](docs/33_LAUNCHER.md)。

> 兜底入口：启动器自带一个**不依赖 node** 的控制台
> `http://127.0.0.1:50100/`（`scripts/start_launcher.ps1` 起），
> 与主界面共用同一套 API。

### 方式二：手工启动（排查时用）

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
# 单元测试（599 个）
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

### 合成记录与日志

```text
GET    /api/v1/history                 分页查询（游标分页，最新在前）
GET    /api/v1/history/stats           统计（成功/失败、RTF、按音色分组）
GET    /api/v1/history/export          导出 CSV / JSON（带筛选）
GET    /api/v1/history/cleanup-history 清理审计记录
GET    /api/v1/history/{id}            单条详情
GET    /api/v1/history/by-request/{rid}  按 request_id 查
DELETE /api/v1/history?before=...      按时间清理（204）

GET    /api/v1/logs                    日志查询（解析 JSON Lines）
GET    /api/v1/logs/files              日志文件列表（含轮转归档，可指定 file_name 查询）
```

**分页用游标不用 OFFSET**：合成记录持续写入，OFFSET 分页会重复或跳过行。
把上一页返回的 `next_cursor` 原样传回即可。

**查询参数**（history）：`voice_id` / `character_id` / `emotion` /
`only_failed` / `only_success` / `text_contains` / `since` / `until` /
`limit`（≤200）/ `cursor`。

**查询参数**（logs）：`limit`（≤1000）/ `level`（可重复传）/
`request_id` / `keyword` / `logger_name` / `only_errors`。

```bash
# 只看失败的合成
curl -s "localhost:50001/api/v1/history?only_failed=true"

# 看错误日志
curl -s "localhost:50001/api/v1/logs?level=ERROR&limit=20"

# 按 request_id 追踪一次请求（rid 从合成响应头 x-request-id 取）
curl -s "localhost:50001/api/v1/logs?request_id=<rid>"
```

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
├─ tests/          # 599 个测试
├─ scripts/        # 启动、测试、验收、迁移脚本
├─ docs/           # 方案、决策、阶段报告
└─ probe/          # 可复现诊断脚本（环境探测、真机联调、情绪审计）
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
| **Phase 2a 音色收尾** | ✅ **完成** | 真引擎接入，186 测试全绿，12 项验收通过，**盲听确认改善** |
| **D1 情绪验证** | ✅ **完成** | **实测结论：情绪指令无效**，改走路线乙（语速/音量/停顿 + LLM 侧情绪） |
| **Phase 2b 表达控制** | ✅ **完成** | 路线乙：语速/音量/停顿 + 指令解析器，235 测试全绿，8 项验收通过 |
| **Phase 4 流式/并发** | ✅ **完成** | 长句首包 5.36s→1.48s（+72.4%），257 测试全绿 |
| **Phase 5 ESP32 桥接** | ✅ **完成** | 协议完全对齐 zjy13，只改 TTS_BASE_URL 即可切换 |
| **Phase 5+ 真机联调** | ✅ **完成** | 板子在线（ai-pet-badge），zjy13→zjy14 实打通，TTSClient 实打验证通过；**已人工确认出声**；479 测试全绿 |
| **Phase 6 Web 界面** | ✅ **完成** | React + TS 8 页，构建通过，30 项验收通过 |
| **Phase 7 稳定发布** | ✅ **完成** | 30 分钟 596 轮零失败、显存零增长；一键启动 + 故障手册 + 验收报告 |

详见 [docs/03_DECISIONS.md](docs/03_DECISIONS.md)、[docs/05_PHASE3_REPORT.md](docs/05_PHASE3_REPORT.md)、[docs/12_PHASE2A_REPORT.md](docs/12_PHASE2A_REPORT.md)、[docs/13_D1_EMOTION_VERDICT.md](docs/13_D1_EMOTION_VERDICT.md)、[docs/14_PHASE2B_REPORT.md](docs/14_PHASE2B_REPORT.md)、[docs/28_DEVICE_E2E_REPORT.md](docs/28_DEVICE_E2E_REPORT.md)。

### 真机联调一键验收

```bash
wsl -e bash /mnt/d/xiangmudaima/zjy14/probe/run_device_e2e.sh
```

前置：zjy14 引擎在跑 + zjy13 WS 服务在跑（8765）+ 板子已连 WiFi。
详见 [docs/28_DEVICE_E2E_REPORT.md](docs/28_DEVICE_E2E_REPORT.md)。

---

## 已知限制

- **模型不支持情绪控制** —— 2026-11-16 实测确认：CosyVoice3-0.5B 的情绪指令无效。
  加入「请用蓝色的语气」这种无意义指令产生的 F0 变化（15.4/72.5 Hz）
  比真正的情绪指令（10.1/24.3 Hz）还大，说明模型只对"prompt 变长"有反应。
  已改走路线乙：语速/音量/停顿控制 + 情绪由 LLM 侧用词表达。
  详见 [docs/13_D1_EMOTION_VERDICT.md](docs/13_D1_EMOTION_VERDICT.md)、[docs/14_PHASE2B_REPORT.md](docs/14_PHASE2B_REPORT.md)。
- **音色已改善但仍非完美** —— 盲听确认较 Phase 0 有改善、数字读法自然、多次采样一致（2026-11-16）。客观相似度 0.9208（超官方基准 0.7559）。剩余差距可能来自参考素材代表性或 0.5B 模型能力上限，非工程问题。
- **仅 1 个角色素材**（elysia），无法验证"跨角色不串音色"（决策 D9）。
- 复用 `~/cosyvoice-venv`，与 CosyVoice 环境耦合；升级依赖前需跑 CosyVoice 冒烟测试。
- 声音档案只记录音频路径，不复制原始文件；外部移动文件会导致校验失败。
- **真机对话轮未做人工验收** —— 链路与 TTS 已实测打通（见
  [docs/28_DEVICE_E2E_REPORT.md](docs/28_DEVICE_E2E_REPORT.md)），
  但"按板子按钮说一句话、听是否出声"需要人在板子前操作，未纳入自动化。
- **板子固件的天气依赖 ICS DNS** —— Windows 移动热点的 DNS 代理
  （`192.168.137.1:53`）可能失效（进程在跑但回 RST），表现为天气卡片
  一直 `--°`，容易误判成"没连 WiFi"。固件已加 DNS 兜底
  （`BADGE_DNS_MAIN`，默认 `223.5.5.5`）并在界面区分
  "未联网 / 校时中 / 天气无数据"。
  见 [docs/27_WIFI_FALSE_ALARM_DIAGNOSIS.md](docs/27_WIFI_FALSE_ALARM_DIAGNOSIS.md)。

> 采样随机性说明：参考文本正确时，同一文本 5 次合成的时长变异系数仅 **0.028**（标准差 0.16s），
> 输出稳定。此前观察到的"最大差 46%"是参考文本错误导致的，不是模型固有随机性。

> 完整的成果与不足清单见 [docs/11_DAILY_SUMMARY_2026-11-09.md](docs/11_DAILY_SUMMARY_2026-11-09.md)。

---

## 相关文档

| 文档 | 内容 |
|---|---|
| [14_PHASE2B_REPORT.md](docs/14_PHASE2B_REPORT.md) | **Phase 2b 报告（表达控制·路线乙）** |
| [15_PHASE6_REPORT.md](docs/15_PHASE6_REPORT.md) | **Phase 6 报告（Web 管理界面）** |
| [16_PHASE4_REPORT.md](docs/16_PHASE4_REPORT.md) | **Phase 4 报告（流式 + 并发）** |
| [17_DEFECT_FIXES.md](docs/17_DEFECT_FIXES.md) | **缺陷修复报告（配置摆设清理 + compile 实现）** |
| [18_PHASE5_REPORT.md](docs/18_PHASE5_REPORT.md) | **Phase 5 报告（设备协议桥接）** |
| [ACCEPTANCE_REPORT.md](docs/ACCEPTANCE_REPORT.md) | **验收报告（只填实测数据）** |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | **部署与运维说明** |
| [33_LAUNCHER.md](docs/33_LAUNCHER.md) | **语音链路一键启动控制台**：网页同时拉起 ASR/LLM/TTS/网关；TTS 钉死为 zjy14 的 :50001；ASR 独立成 :50002；含 WSL 环境限制说明 |
| [32_HEALTH_CHECK_2026_11_25.md](docs/32_HEALTH_CHECK_2026_11_25.md) | **以 zjy13 为基准的全面体检**：7 个隐患（P0 事件循环冻结、CSV 注入、符号链接绕过、嵌套事务）+ 4 处契约漂移（`lead_sec`、`FIRMWARE_RING_MS`、`token_max_hop_len`） |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | **故障排查手册** |
| [19_PHASE8_STREAM_UI.md](docs/19_PHASE8_STREAM_UI.md) | **前端流式播放 + 真机联调准备** |
| [20_DAILY_SUMMARY_2026-11-16.md](docs/20_DAILY_SUMMARY_2026-11-16.md) | **当日总结（成果·不足·缺陷）** |
| [21_HISTORY_LOGS_API.md](docs/21_HISTORY_LOGS_API.md) | **合成记录与日志查询 API** |
| [22_HISTORY_ENHANCEMENTS.md](docs/22_HISTORY_ENHANCEMENTS.md) | **历史记录四个限制的补齐（FTS/导出/归档/自动清理）** |
| [23_HISTORY_ROUND2.md](docs/23_HISTORY_ROUND2.md) | **第二轮补齐（跨文件搜索/相关度排序/导出进度/日志审计）** |
| [24_DEVICE_MONITOR_PROMPT.md](docs/24_DEVICE_MONITOR_PROMPT.md) | **设备状态监控 Web 端开发提示词** |
| [25_DEVICE_MONITOR_REPORT.md](docs/25_DEVICE_MONITOR_REPORT.md) | **设备监控 Web 端实现报告** |
| [26_DAILY_SUMMARY_2026-11-18.md](docs/26_DAILY_SUMMARY_2026-11-18.md) | **当日总结（成果·不足·缺陷）** |
| [27_WIFI_FALSE_ALARM_DIAGNOSIS.md](docs/27_WIFI_FALSE_ALARM_DIAGNOSIS.md) | **"板子没连 WiFi"误判排查（实为天气 DNS 失败）** |
| [28_DEVICE_E2E_REPORT.md](docs/28_DEVICE_E2E_REPORT.md) | **真机联调报告（Phase 5 补完：真机在环）** |
| [29_INTEGRATION_BUG_AUDIT.md](docs/29_INTEGRATION_BUG_AUDIT.md) | **zjy13↔zjy14 集成缺陷排查（7 修 + 2 项实测推翻）** |
| [30_EMOTION_CONTROL_AUDIT.md](docs/30_EMOTION_CONTROL_AUDIT.md) | **情绪控制专项审计**：模型支持但调用路径错，`zero_shot_spk_id` 静默丢弃指令 |
| [probe/README.md](probe/README.md) | **诊断脚本用法**：6 个情绪审计脚本的跑法与判据 |
| [13_D1_EMOTION_VERDICT.md](docs/13_D1_EMOTION_VERDICT.md) | **D1 情绪验证结论（情绪指令无效）** |
| [12_PHASE2A_REPORT.md](docs/12_PHASE2A_REPORT.md) | Phase 2a 报告（真引擎接入） |
| [11_DAILY_SUMMARY_2026-11-09.md](docs/11_DAILY_SUMMARY_2026-11-09.md) | 2026-11-09 当日总结（成果 + 不足） |
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
