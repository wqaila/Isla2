# 部署与运维说明

> 面向：在本机部署 zjy14 并接入 zjy13 设备链路。
> 所有步骤均在本机验证过。

---

## 一、架构与部署边界

```text
┌─────────────────────────────────────────────────────────┐
│ Windows（D:\xiangmudaima\zjy14\）                        │
│   ├─ 代码（编辑器直接改）                                  │
│   ├─ web/ 前端（Vite 构建静态文件）                        │
│   └─ zjy13/server/ws/ WebSocket 服务（复用，不改）         │
├─────────────────────────────────────────────────────────┤
│ WSL2 Ubuntu 24.04（~/cosyvoice-venv）                    │
│   ├─ CosyVoice3 模型（~/models/Fun-CosyVoice3-0.5B-2512）│
│   ├─ zjy14 引擎服务（127.0.0.1:50001）                    │
│   └─ 数据/日志（~/zjy14-data/）                           │
├─────────────────────────────────────────────────────────┤
│ ESP32-S3（固件已烧录，不改）                              │
│   └─ 通过局域网 WebSocket 连 zjy13                        │
└─────────────────────────────────────────────────────────┘
```

**关键边界**：
- 模型推理**只能在 WSL**（CosyVoice 的 `deepspeed` / `onnxruntime-gpu` /
  `tensorrt` 官方只支持 Linux）
- 数据与日志放 **WSL 本地**（`~/zjy14-data/`），不放 `/mnt/d`
  —— 跨文件系统 IO 慢，SQLite 在其上并发写有风险
- 代码放 `/mnt/d`，Windows 编辑器可直接改

---

## 二、首次部署

### 2.1 前置条件

| 项 | 要求 | 检查命令 |
|---|---|---|
| GPU | NVIDIA，≥ 8GB 显存（实测 RTX 5070 12GB） | `nvidia-smi` |
| 驱动 | 支持 CUDA 12.8 | `nvidia-smi` 右上角 |
| WSL2 | Ubuntu 24.04 | `wsl -l -v` |
| Python | 3.10（WSL 内） | `python -V` |
| 磁盘 | ≥ 15GB（模型 9.1GB） | `df -h` |

### 2.2 准备 CosyVoice 环境

若 `zjy13` 已跑通，**这步可跳过**（复用其环境）。

```bash
# 在 WSL 内
git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git ~/CosyVoice
cd ~/CosyVoice
python3.10 -m venv ~/cosyvoice-venv
source ~/cosyvoice-venv/bin/activate
pip install -r requirements.txt

# 下载模型（9.1GB）
python -c "
from modelscope import snapshot_download
snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512',
                  local_dir='/home/user/models/Fun-CosyVoice3-0.5B-2512')
"
```

### 2.3 安装 zjy14

```bash
# 项目代码已在 D:\xiangmudaima\zjy14\，WSL 通过 /mnt/d 访问
cd /mnt/d/xiangmudaima/zjy14
source ~/cosyvoice-venv/bin/activate
pip install -r requirements.txt    # 全部取 venv 已有版本，不会升级
```

**注意**：`requirements.txt` 里所有版本都锁定为 `~/cosyvoice-venv`
中已存在的版本，目的是避免 pip 触发依赖升级污染 CosyVoice 环境。

### 2.4 迁移角色与声音

```bash
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && \
  source ~/cosyvoice-venv/bin/activate && \
  PYTHONPATH=. python scripts/migrate_elysia.py"
```

预期输出：
```
[检测] 可用（评分 0.93）
[创建] 声音档案 elysia  score=0.9307  key=639c511d
[创建] 角色 elysia  绑定声音 elysia
```

脚本幂等，重复执行会跳过已存在的记录。

### 2.5 首次启动

```bash
# 方式 A：Windows 侧一键（推荐）
.\scripts\start.ps1 cosyvoice3

# 方式 B：WSL 内直接启动
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/start.sh cosyvoice3
```

预期：约 19–22 秒后看到 `CosyVoice3 引擎就绪`。

### 2.6 验证

```bash
# 健康检查
curl http://127.0.0.1:50001/health

# 合成测试
curl -X POST http://127.0.0.1:50001/synthesize \
  -H 'Content-Type: application/json' \
  -d '{"text":"今天天气真不错","voice_id":"elysia"}' \
  --output test.wav
```

`/health` 应返回：
```json
{
  "ok": true,
  "engine": "cosyvoice3",
  "sample_rate": 24000,
  "diagnostics": {
    "text_frontend": "wetext",
    "voice_count": 1,
    "precision": "fp32",
    "compile_applied": false
  }
}
```

---

## 三、接入 zjy13 设备链路

**只需改一个环境变量。**

```bash
# zjy13/.env
TTS_BASE_URL=http://127.0.0.1:50001   # 原 :50000
```

重启 zjy13 的 WS 服务即生效。

**回滚**：改回 `:50000`。

**为什么不用改代码**：zjy14 的 `/synthesize`、`/synthesize_stream`、
`/health` 在路径、响应头、错误码语义上与 zjy13 的 `TTSClient` 完全对齐
（逐项验证见 `docs/18_PHASE5_REPORT.md` 第二节）。

---

## 四、Web 管理界面

### 4.1 开发模式

```bash
# 终端 1：后端
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/start.sh cosyvoice3

# 终端 2：前端
cd D:\xiangmudaima\zjy14\web
& "D:\New Folder\npm.cmd" run dev
# 打开 http://localhost:5173
```

### 4.2 生产构建

```bash
cd web
& "D:\New Folder\npm.cmd" run build
# 产物在 web/dist/
```

**部署方式**（二选一）：

**方案 A：Nginx / Caddy 反代（推荐）**
```nginx
server {
    listen 8080;
    root /path/to/zjy14/web/dist;
    index index.html;

    # SPA 路由回退
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 反代到后端
    location /api/ { proxy_pass http://127.0.0.1:50001; }
    location /health { proxy_pass http://127.0.0.1:50001; }
    location /synthesize { proxy_pass http://127.0.0.1:50001; }
}
```

**方案 B：挂到 FastAPI StaticFiles**
需修改后端代码（当前未内置）。

**注意**：按决策 D10，后端默认仅监听 `127.0.0.1`。
生产部署若需局域网访问，应通过反代暴露，并配置 `auth_token`。

---

## 五、配置说明

配置文件：`configs/gpu.yaml`

### 5.1 关键配置

| 字段 | 默认 | 说明 |
|---|---|---|
| `engine` | `stub` | `stub` 占位（快速验证链路）/ `cosyvoice3` 真模型 |
| `precision` | `fp32` | **实测 fp16 更慢 7.1%、显存 +27%**，勿改 |
| `max_concurrent_requests` | 1 | 先单并发稳定 |
| `max_queue_size` | 8 | 队列满返回 429 |
| `stream_enabled` | true | 流式开关 |
| `stream_char_threshold` | 15 | 短句非流式、长句流式 |
| `text_frontend` | `auto` | `auto`/`wetext`/`none` |
| `token_hop_len_reset` | true | **必须开**，修上游跨调用污染 |
| `min_text_ratio` | 0.5 | 合成文本/参考文本长度比阈值 |
| `enable_compile` | false | torch.compile，实测 8.7% 收益但增加冷启动 |
| `host` / `port` | `127.0.0.1` / 50001 | 默认仅回环 |
| `auth_token` | `""` | 监听非回环时**必填**，否则启动报错 |

### 5.2 环境变量覆盖

前缀 `ZJY14_` + 大写字段名：

```bash
ZJY14_ENGINE=cosyvoice3
ZJY14_PORT=50002
ZJY14_ENABLE_COMPILE=true
ZJY14_TEXT_FRONTEND=wetext
```

### 5.3 配置校验机制

每个字段必须在 `custom_voice/config/schema.py` 的 `CONSUMED_FIELDS`
登记消费方，否则**启动报错**。

这是刻意设计，兑现原提示词"不能留下配置看似生效但实际未使用的参数"。

**审查工具**：
```bash
wsl -e bash -lc "source ~/cosyvoice-venv/bin/activate && \
  cd /mnt/d/xiangmudaima/zjy14 && PYTHONPATH=. python probe/_audit_config_usage.py"
```

---

## 六、运维

### 6.1 日志

```bash
# 位置
~/zjy14-data/logs/zjy14.log

# 最近 50 行
tail -n 50 ~/zjy14-data/logs/zjy14.log

# 只看警告与错误
grep -E '"level": "(WARNING|ERROR)"' ~/zjy14-data/logs/zjy14.log | tail -30

# 按 request_id 追踪
grep '<request_id>' ~/zjy14-data/logs/zjy14.log
```

格式：JSON Lines，每行一个事件。敏感字段自动脱敏。

### 6.2 健康检查

```bash
# 基础
curl -s localhost:50001/health | python3 -m json.tool

# 调度器统计
curl -s localhost:50001/api/v1/system/status | python3 -c "
import json,sys
d = json.load(sys.stdin)
print('调度器:', d['scheduler'])
print('桥接:', d['bridge']['protocol_version'])
"

# GPU
curl -s localhost:50001/api/v1/system/gpu | python3 -m json.tool
```

### 6.3 数据备份

```bash
# 备份数据库与日志
wsl -e bash -lc "tar czf ~/zjy14-backup-$(date +%Y%m%d).tar.gz \
  ~/zjy14-data/"

# 恢复
wsl -e bash -lc "tar xzf ~/zjy14-backup-YYYYMMDD.tar.gz -C ~/"
```

**注意**：参考音频不在备份范围（声音档案只记录路径）。
若需完整备份，需额外备份 `zjy13/server/characters/voices/`。

### 6.4 升级

```bash
# 1. 备份数据
wsl -e bash -lc "tar czf ~/zjy14-backup.tar.gz ~/zjy14-data/"

# 2. 拉取新代码
cd D:\xiangmudaima
git pull

# 3. 跑测试
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/run_tests.sh

# 4. 跑验收
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/verify_phase1.sh

# 5. 重启服务
```

**回滚**：
```bash
git log --oneline -10        # 找到上一个可用提交
git checkout <commit> -- zjy14
```

### 6.5 常见运维任务

| 任务 | 命令 |
|---|---|
| 查看所有角色 | `curl -s localhost:50001/api/v1/characters` |
| 查看所有声音 | `curl -s localhost:50001/api/v1/speakers` |
| 重新检测声音质量 | `curl -X POST localhost:50001/api/v1/speakers/elysia/validate` |
| 清空显存缓存 | 重启服务（`engine.shutdown()` 会调 `empty_cache`） |
| 查看合成历史 | `sqlite3 ~/zjy14-data/custom_voice.db "SELECT * FROM synthesis_logs ORDER BY id DESC LIMIT 10;"` |

---

## 七、性能参考

实测数据（RTX 5070 12GB，2026-11-16）：

| 指标 | 值 |
|---|---|
| 模型加载 | 13.5–22s |
| 预热 | 3.2s |
| RTF | 0.57–0.82，中位 0.64 |
| 峰值显存 | 3.45–3.54 GB |
| 首包延迟（短句） | 1.60s（非流式） |
| 首包延迟（长句） | 1.48s（流式）/ 5.36s（非流式） |
| 采样稳定性 | 时长变异系数 0.028 |

**12GB 显存预算**：
```
总显存        12227 MB
桌面已占       ~2000 MB
TTS 峰值        3542 MB
ASR 估算       ~2048 MB（来自 zjy13）
剩余           ~4600 MB
```

---

## 八、安全检查清单

部署前确认：

- [ ] `host` 为 `127.0.0.1`（默认）
- [ ] 若对外开放，`auth_token` 已设置且足够强
- [ ] 参考音频为自有或已授权素材
- [ ] `.env` / `auth_token` 未提交到 git
- [ ] 日志中无 API Key 明文（已自动脱敏）
- [ ] 备份了数据库

---

## 九、相关文档

| 文档 | 内容 |
|---|---|
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 故障排查（症状 → 原因 → 解决） |
| [ACCEPTANCE_REPORT.md](ACCEPTANCE_REPORT.md) | 验收报告（只填实测数据） |
| [03_DECISIONS.md](03_DECISIONS.md) | 10 项决策记录 |
| [02_IMPLEMENTATION_PLAN.md](02_IMPLEMENTATION_PLAN.md) | 落地方案与架构 |
