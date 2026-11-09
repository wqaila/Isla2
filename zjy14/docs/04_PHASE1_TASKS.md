# Phase 1：工程骨架 —— 详细任务清单

> 目标：搭出一个**能跑、能测、能扩展**的骨架，不含业务逻辑。
> 估算：2–3 人日。
> 前置：决策见 [03_DECISIONS.md](03_DECISIONS.md)。

---

## 一、Phase 1 到底在做什么

一句话：**把"将来要改的地方"先挖好槽位，让 Phase 2 之后只填内容、不改结构。**

具体解决 4 件事：

| # | 要解决的问题 | 不解决的后果 |
|:-:|---|---|
| 1 | 代码放哪、怎么分层、怎么防止 Web 层碰模型 | 后期改一个需求动全身 |
| 2 | 配置怎么管、怎么保证字段真的生效 | 原文档明令禁止的"配置看似生效但实际未使用" |
| 3 | 引擎怎么被调用、Phase 2 换实现时不改调用方 | 情绪策略一换就要改所有路由 |
| 4 | 日志怎么串起来、出错怎么报 | 出问题查不到 request_id 全链路 |

**Phase 1 结束时应该能**：
- `pytest` 全绿
- `curl /health` 返回真实模型状态（模型未加载时明确报 503，不假报 OK）
- `curl /api/v1/system/gpu` 返回真实显存
- 配置文件改错一个字段，启动时明确报错并指出字段名
- 合成接口返回一份**占位音频**（stub），链路通但没接模型

---

## 二、关键前置决策（需要先定）

### P1-1：跑在哪个 Python 环境

**实测现状**：

| 环境 | torch | fastapi | uvicorn | pydantic | soundfile |
|---|---|---|---|---|---|
| Windows `D:\xiangmudaima\venv` | ❌ | ❌ | ❌ | ❌ | ❌ |
| Windows `zjy13\.venv` | ❌ | ❌ | ❌ | ❌ | ❌ |
| **WSL `~/cosyvoice-venv`** | ✅ 2.8.0+cu128 | ✅ 0.115.6 | ✅ 0.30.0 | ✅ 2.7.0 | ✅ 0.12.1 |

**结论：只能跑在 WSL 的 `cosyvoice-venv` 里**，因为模型和 torch 都在那。

`zjy13/_wheels` 里虽然有 Windows 版 `torch-2.8.0+cu128` 轮子（3.3GB），但**未安装**，且 CosyVoice 官方只支持 Linux 路径（`requirements.txt` 里 `deepspeed`/`onnxruntime-gpu`/`tensorrt` 全是 `sys_platform == 'linux'`）。不折腾 Windows 原生。

**三个选项**：

| 选项 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A（推荐）** | 复用 `~/cosyvoice-venv` | 零安装，torch 现成 | 与 CosyVoice 环境耦合 |
| B | 新建 `~/zjy14-venv`，重装 torch | 环境干净 | 多下载 2GB+，多维护一套 |
| C | 新建 venv 但 `--system-site-packages` 指向系统 python | 隔离部分依赖 | 系统 python 没 torch，仍要重装 |

选 A 的风险：zjy14 的依赖升级可能影响 CosyVoice。缓解：`requirements.txt` 锁死版本，升级前跑 CosyVoice 冒烟测试。

### P1-2：代码路径与运行路径的映射

```text
代码位置（Windows）：  D:\xiangmudaima\zjy14\
运行位置（WSL）：      /mnt/d/xiangmudaima/zjy14/
```

WSL 通过 `/mnt/d` 直接访问，**不需要复制代码**。但要注意：
- `/mnt/d` 跨文件系统 IO 慢，**不要**把 SQLite 数据库放在 `/mnt/d`（用 WSL 本地 `~/zjy14-data/`）
- 日志同理，放 WSL 本地
- 代码本身读多写少，放 `/mnt/d` 没问题

**建议的路径规划**：

| 内容 | 路径 | 理由 |
|---|---|---|
| 代码 | `/mnt/d/xiangmudaima/zjy14/` | 与 Windows 编辑器共享 |
| SQLite | `~/zjy14-data/custom_voice.db` | 避免跨系统 IO |
| 日志 | `~/zjy14-data/logs/` | 同上 |
| 音频输出 | `~/zjy14-data/audio/` | 同上 |
| 参考音频（只读） | `/mnt/d/xiangmudaima/zjy13/server/characters/voices/` | 复用现有 |
| 模型 | `~/models/Fun-CosyVoice3-0.5B-2512` | 已有 |

### P1-3：接口契约必须兼容 zjy13（D2 硬约束）

zjy13 的 `TTSClient` 已经在用这套契约，**Phase 1 必须原样实现**，否则 zjy13 切不过来。

**`POST /synthesize`**

请求：
```json
{"text": "...", "voice_id": "elysia", "speed": 1.0}
```

响应：**WAV 二进制** + 响应头

| 头 | 示例 | 必需 |
|---|---|---|
| `x-sample-rate` | `24000` | ✅ |
| `x-duration` | `3.240` | ✅ |
| `x-total-ms` | `2670.5` | ✅ |
| `x-rtf` | `0.7032` | ✅ |
| `x-engine` | `cosyvoice3` | ✅ |
| `x-voice-id` | `elysia` | ✅ |

状态码语义（zjy13 已依赖）：
- `200` 成功
- `400` 参数错误 → zjy13 抛 `TTSBadRequestError`
- `5xx` 服务内部错误 → zjy13 抛 `TTSError`
- 连接失败 → zjy13 抛 `TTSUnavailableError`（**不允许静默回退云端**）

**`GET /health`**

返回 JSON，至少含：`ok`、`engine`、`model`、`sample_rate`、`device`、`gpu`、`torch`、`load_s`、`warmup_s`、`voices`

**扩展方式**：新增字段（`emotion` / `style` / `instruction`）全部设为**可选**，旧调用不传时走默认值。这样 zjy13 零改动。

---

## 三、目录结构（Phase 1 只建骨架）

```text
zjy14/
├─ custom_voice/
│  ├─ __init__.py
│  ├─ config/
│  │  ├─ __init__.py
│  │  ├─ loader.py           # YAML 加载 + 环境变量覆盖
│  │  └─ schema.py           # Pydantic 配置模型 + 校验
│  ├─ core/
│  │  ├─ __init__.py
│  │  ├─ errors.py           # 异常体系
│  │  ├─ logging.py          # 结构化日志 + request_id
│  │  └─ paths.py            # 路径解析（Windows/WSL 双环境）
│  ├─ db/
│  │  ├─ __init__.py
│  │  ├─ models.py           # SQLite 表定义
│  │  ├─ session.py          # 连接管理
│  │  └─ migrate.py          # 建表/迁移
│  ├─ inference/
│  │  ├─ __init__.py
│  │  └─ engine.py           # ★ 引擎抽象 + StubEngine（Phase 2 填真实现）
│  ├─ server/
│  │  ├─ __init__.py
│  │  ├─ app.py              # FastAPI 实例 + 生命周期
│  │  ├─ middleware.py       # request_id 注入
│  │  └─ routes/
│  │     ├─ __init__.py
│  │     ├─ health.py        # /health
│  │     ├─ system.py        # /api/v1/system/{status,gpu}
│  │     └─ synthesis.py     # /synthesize（Phase 1 返回 stub 音频）
│  └─ schemas/
│     ├─ __init__.py
│     └─ synthesis.py        # 请求/响应 Pydantic 模型
├─ configs/
│  ├─ gpu.yaml               # 已按实测修正（见方案 6.1）
│  └─ emotions.yaml          # 情绪映射（Phase 1 只放占位结构）
├─ tests/
│  ├─ conftest.py
│  ├─ test_config.py
│  ├─ test_errors.py
│  ├─ test_logging.py
│  ├─ test_db.py
│  ├─ test_engine_stub.py
│  └─ test_api_contract.py   # ★ 验证与 zjy13 契约兼容
├─ scripts/
│  ├─ run_server.sh          # WSL 启动
│  └─ run_tests.sh           # WSL 跑测试
├─ pyproject.toml
├─ requirements.txt
├─ .gitignore
└─ docs/
```

**分层铁律**：`server/routes/` 只 import `schemas/` 和 `inference/engine.py`，**绝不 import `cosyvoice.*`**。用测试强制（见 T7）。

---

## 四、任务清单

### T1. 项目骨架与依赖（0.3 天）

- [ ] 建目录结构、`__init__.py`
- [ ] `pyproject.toml`（项目元数据、pytest 配置、ruff 配置）
- [ ] `requirements.txt` 锁版本：
  ```
  fastapi==0.115.6
  uvicorn==0.30.0
  pydantic==2.7.0
  PyYAML==6.0.3
  soundfile==0.12.1
  numpy==1.26.4
  httpx==0.28.1
  pytest==9.1.1
  ```
  > 全部取 WSL `cosyvoice-venv` 已有版本，避免触发依赖升级
- [ ] `.gitignore`：排除 `data/`、`_audio_probe/`、`*.wav`、`__pycache__`、`.venv`、`*.db`

**验收**：`pip install -r requirements.txt` 无新增包（全部已满足）

### T2. 配置系统（0.4 天）★ 重点

这是原文档点名的坑：**"不能留下配置看似生效但实际未使用的参数"**。

- [ ] `configs/gpu.yaml`：按方案 6.1 的 12 个字段
- [ ] `config/schema.py`：Pydantic 模型，带类型校验和范围校验
- [ ] `config/loader.py`：YAML 加载 + `ZJY14_*` 环境变量覆盖
- [ ] **消费登记机制**：每个配置字段必须在 `CONSUMED_FIELDS` 集合里登记，启动时校验
  ```python
  # 未登记的字段 → 启动报错，强制开发者要么用上、要么删掉
  if set(schema.model_fields) - CONSUMED_FIELDS:
      raise ConfigError(f"配置字段未生效: {undeclared}")
  ```
- [ ] 非法值报错要指出**字段名 + 当前值 + 合法范围**

**验收**：
- 改 `precision: fp16` → 启动正常（fp16 是合法值，但 engine 会记录警告）
- 改 `max_concurrent_requests: 0` → 启动报错，指出字段和范围
- 加一个未实现的字段 → 启动报错，指出该字段未生效

### T3. 异常体系（0.2 天）

- [ ] `core/errors.py`：
  ```
  ZJY14Error (基类)
  ├─ ConfigError
  ├─ EngineError
  │  ├─ EngineNotReadyError    → HTTP 503
  │  ├─ SynthesisError          → HTTP 500
  │  └─ InvalidRequestError     → HTTP 400
  ├─ SpeakerError
  │  ├─ SpeakerNotFoundError    → HTTP 404
  │  └─ AudioQualityError       → HTTP 422
  └─ VRAMError                  → HTTP 507
  ```
- [ ] 每个异常带 `code`（机器可读）+ `message`（人可读）+ `detail`
- [ ] FastAPI 异常处理器统一转 JSON，**不泄露堆栈到响应体**

**验收**：抛每个异常，HTTP 状态码和 JSON 结构符合预期

### T4. 结构化日志（0.3 天）

- [ ] `core/logging.py`：JSON 行格式，字段：`ts`、`level`、`request_id`、`event`、`duration_ms`、`extra`
- [ ] `request_id` 用 `contextvars` 贯穿，中间件自动注入和回传响应头 `x-request-id`
- [ ] 敏感字段脱敏：`api_key`、`auth_token` 只记录是否设置
- [ ] 日志落 `~/zjy14-data/logs/zjy14.log`，按天轮转

**验收**：一次请求的日志里，`request_id` 在入口、引擎调用、出口三处一致

### T5. SQLite 数据层（0.4 天）

Phase 1 只建表和连接管理，不做 CRUD 业务。

- [ ] `db/models.py`：表结构
  ```sql
  characters        -- 角色档案
  speakers          -- 声音档案
  reference_audio   -- 参考音频元数据
  synthesis_logs    -- 合成记录（含指标）
  metrics           -- 性能指标时序
  schema_version    -- 迁移版本
  ```
- [ ] `db/session.py`：连接池、WAL 模式、外键开启
- [ ] `db/migrate.py`：幂等建表，版本号递增
- [ ] 数据库路径：`~/zjy14-data/custom_voice.db`（**不放 /mnt/d**）

**验收**：
- 首次启动自动建表
- 重复启动不报错（幂等）
- `PRAGMA foreign_keys` 已开启

### T6. 引擎抽象 + Stub 实现（0.5 天）★ 重点

Phase 1 **不加载真模型**，但要把接口定死，让 Phase 2 只换实现。

- [ ] `inference/engine.py`：
  ```python
  class TTSEngine(Protocol):
      def load(self) -> None: ...
      def is_ready(self) -> bool: ...
      def list_voices(self) -> list[str]: ...
      def synthesize(self, req: SynthesisRequest) -> SynthesisResult: ...
      def synthesize_stream(self, req) -> Iterator[AudioChunk]: ...  # Phase 4 才真实现
      def stats(self) -> EngineStats: ...
  ```
- [ ] `StubEngine`：生成一段固定时长的正弦波 WAV，**让链路可测**
  - 时长 = 字符数 × 0.2s（便于测试断言）
  - 24kHz / 单声道 / 16bit（与固件一致）
- [ ] `EngineStats`：`load_s`、`warmup_s`、`device`、`gpu_name`、`peak_vram_mb`、`sample_rate`

**为什么用 Stub**：Phase 1 就能跑通 HTTP 链路和契约测试，不用等模型加载（10s+），测试快且稳定。Phase 2 换成 `CosyVoiceEngine` 时，**路由代码一行不改**。

**验收**：`StubEngine` 产出的 WAV 能被 `soundfile` 正常读取，采样率 24000

### T7. FastAPI 骨架（0.5 天）

- [ ] `server/app.py`：FastAPI 实例、lifespan（启动加载配置+建表，关闭释放）
- [ ] `server/middleware.py`：request_id 注入
- [ ] 三个端点：
  - `GET /health` → 模型未就绪返回 **503**（不假报 OK）
  - `GET /api/v1/system/status` → 版本、配置摘要（脱敏）、引擎状态
  - `GET /api/v1/system/gpu` → 真实显存（`torch.cuda.mem_get_info`），torch 不可用时返回明确说明而非崩溃
  - `POST /synthesize` → 走 StubEngine，返回 WAV + 6 个响应头
- [ ] **D10 安全约束**：配置 `host: 0.0.0.0` 但未设 `auth_token` 时，启动**直接报错退出**
- [ ] 默认 `127.0.0.1:50001`

**验收**：
- `curl -i -X POST localhost:50001/synthesize -d '{"text":"你好","voice_id":"elysia"}'` 返回 WAV + 全部响应头
- `curl localhost:50001/health` 在 stub 模式下返回 `ok: true`，未加载时 `503`
- 设 `host: 0.0.0.0` 无 token → 启动失败并打印原因

### T8. 测试骨架（0.4 天）

- [ ] `tests/conftest.py`：pytest fixture（临时 DB、测试配置、TestClient）
- [ ] `test_config.py`：合法/非法配置、字段消费校验
- [ ] `test_errors.py`：异常 → HTTP 状态码映射
- [ ] `test_logging.py`：request_id 贯穿
- [ ] `test_db.py`：建表幂等、外键开启
- [ ] `test_engine_stub.py`：Stub 产出格式正确
- [ ] `test_api_contract.py`：★ **契约测试** —— 逐项断言 6 个响应头存在且类型正确，400/503 语义正确
- [ ] **架构测试**：扫描 `server/` 下所有 import，断言**没有** `cosyvoice` 字样

**验收**：`pytest -v` 全绿，覆盖率 ≥70%（骨架阶段够用）

### T9. 启动脚本与首次提交（0.2 天）

- [ ] `scripts/run_server.sh`：WSL 启动（激活 venv、设路径、起 uvicorn）
- [ ] `scripts/run_tests.sh`：WSL 跑测试
- [ ] `README.md`：怎么跑、端口、配置说明
- [ ] **首次 git 提交**（D3），`.gitignore` 已就位
- [ ] 提交前检查：`git status` 无 `data/`、无 `*.wav`、无 `*.db`

**验收**：`git log` 有一条 zjy14 的初始提交；`git status` 干净

---

## 五、Phase 1 不做什么（防止范围蔓延）

明确排除，避免拖工期：

| 不做 | 原因 | 何时做 |
|---|---|---|
| 加载真 CosyVoice 模型 | 用 Stub 即可验证链路 | Phase 2 |
| 混合调用模式 | 属于引擎实现 | Phase 2 |
| 情绪映射逻辑 | 等 D1 盲听结论 | Phase 2 |
| 角色/声音 CRUD | 只建表不写业务 | Phase 3 |
| 真流式 | 接口留好，实现后置 | Phase 4 |
| Web 界面 | 独立阶段 | Phase 6 |
| 并发/队列调度 | 先单请求跑通 | Phase 4 |
| 鉴权实现 | 只做"无 token 不许开 0.0.0.0"的守门 | Phase 7 |

---

## 六、Phase 1 的风险

| # | 风险 | 缓解 |
|:-:|---|---|
| 1 | 复用 `cosyvoice-venv` 可能污染 CosyVoice 环境 | requirements 锁死已装版本；升级前跑 CosyVoice 冒烟 |
| 2 | `/mnt/d` 跨系统 IO 影响 SQLite | 数据库和日志放 WSL 本地 |
| 3 | Stub 与真引擎行为差异，Phase 2 才发现契约不够 | `test_api_contract.py` 按 zjy13 实际消费方式断言 |
| 4 | 配置"消费登记"机制过严，开发时频繁报错 | 提供 `--allow-unused-config` 开发开关（仅 dev） |
| 5 | Windows 编辑器与 WSL 换行符差异 | `.gitattributes` 设 `* text=auto eol=lf` |

---

## 七、Phase 1 完成标志

一份可验收的检查清单：

```text
[ ] pytest 全绿，覆盖 config/errors/logging/db/engine/api-contract
[ ] /health 在模型未加载时返回 503（不假报 OK）
[ ] /api/v1/system/gpu 返回真实显存数字
[ ] POST /synthesize 返回合法 WAV + 6 个响应头
[ ] 配置字段未消费时启动报错
[ ] host=0.0.0.0 且无 token 时启动失败
[ ] 架构测试：server/ 下无 cosyvoice import
[ ] request_id 在日志中贯穿
[ ] SQLite 建表幂等，位于 WSL 本地
[ ] git 首次提交完成，.gitignore 生效
```

**Phase 1 之后，Phase 2 只需要**：
1. 写 `CosyVoiceEngine` 实现 `TTSEngine` 协议
2. 在 `configs/` 里把 `engine: stub` 改成 `engine: cosyvoice3`
3. 路由、测试、日志全部不动

这就是 Phase 1 的核心价值：**把变化点隔离出来**。

---

## 八、需要你确认的 2 件事

**Q1（P1-1）**：复用 `~/cosyvoice-venv`，还是新建独立 venv？
- 推荐复用：零安装，torch 现成
- 代价：与 CosyVoice 环境耦合，升级需谨慎

**Q2（P1-2）**：数据库和日志放 WSL 本地（`~/zjy14-data/`），还是放 `D:\xiangmudaima\zjy14\data\`？
- 推荐 WSL 本地：避免跨系统 IO，SQLite 在 `/mnt/d` 上并发写有风险
- 代价：Windows 侧看日志要去 WSL 里翻（可加个同步脚本）
