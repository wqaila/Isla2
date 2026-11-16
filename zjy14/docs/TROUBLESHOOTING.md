# 故障排查手册

> 本手册的每一条都来自本项目开发过程中**实际遇到并解决**的问题，不是猜测。
> 按「症状 → 原因 → 解决」组织，便于快速定位。

---

## 一、启动失败

### 1.1 `ModuleNotFoundError: No module named 'cosyvoice'`

**原因**：不在 WSL 的 `~/cosyvoice-venv` 环境中，或 `~/CosyVoice` 不在 `sys.path`。

**解决**：
```bash
# 确认在正确环境
which python    # 应输出 /home/user/cosyvoice-venv/bin/python

# 若不对，手动激活
source ~/cosyvoice-venv/bin/activate
```

引擎的 `load()` 会自动把 `~/CosyVoice` 与 `third_party/Matcha-TTS` 加入
`sys.path`，但前提是这两个目录存在。

---

### 1.2 `ModuleNotFoundError: No module named 'torch'`

**原因**：Windows 侧没有 torch（这是设计如此）。

**解决**：CosyVoice3 只能在 WSL 里跑。用 `scripts/start.ps1` 或
`scripts/start.sh`，不要直接 `python -m custom_voice.server`（Windows 侧）。

**背景**：`zjy13/_wheels` 里有 Windows 版 torch 轮子（3.3GB），但
CosyVoice 的 `requirements.txt` 里 `deepspeed` / `onnxruntime-gpu` / `tensorrt`
全是 `sys_platform == 'linux'`，Windows 原生路径官方不支持。

---

### 1.3 启动卡住 90 秒以上

**症状**：日志里出现大量 `Downloading [xx.fst]`，然后 403 重试。

**原因**：`wetext` 的 `__init__` 无条件调 `snapshot_download()`，
无 token 时会先下载部分文件（实测 57 秒）再失败。

**已修复**：引擎在构造 `CosyVoice3` 前会 monkey-patch
`modelscope.snapshot_download` 让它立刻抛异常（`_block_modelscope_download`）。
启动时间从 90s+ 降到 19s。

**若仍遇到**：
```bash
# 检查本地是否有 wetext 资源
ls ~/.cache/modelscope/hub/pengzhendong/wetext/zh/tn/
# 应有 tagger.fst 与 verbalizer.fst

# 若缺失，需要一次联网下载（可配 modelscope token）
```

---

### 1.4 `配置校验失败：以下配置字段没有登记消费方`

**原因**：新增了配置字段但没在 `CONSUMED_FIELDS` 登记。
这是刻意设计——防止"配置看似生效但实际未使用"。

**解决**：在 `custom_voice/config/schema.py` 的 `CONSUMED_FIELDS`
里登记该字段的消费方，或从配置中删除该字段。

**审查工具**：
```bash
wsl -e bash -lc "source ~/cosyvoice-venv/bin/activate && \
  cd /mnt/d/xiangmudaima/zjy14 && PYTHONPATH=. python probe/_audit_config_usage.py"
```

---

### 1.5 `host='0.0.0.0' 表示对外开放监听，必须同时设置 auth_token`

**原因**：安全守门（决策 D10）。监听非回环地址时必须配 token。

**解决**：
```yaml
# configs/gpu.yaml
host: 127.0.0.1        # 仅本机（推荐）
# 或
host: 0.0.0.0
auth_token: "your-token-here"
```

---

### 1.6 `可用显存不足：需要至少 1024 MB`

**原因**：启动时的显存检查。桌面/其他程序占用了显存。

**解决**：
```bash
# 查看谁在占显存
nvidia-smi

# 临时调低要求（不推荐长期）
ZJY14_MEMORY_SAFETY_MARGIN_MB=256
```

---

## 二、合成失败

### 2.1 `RuntimeError: Calculated padded input size per channel: (3). Kernel size: (4)`

**这是本项目遇到过最隐蔽的错误，有三种原因。**

#### 原因 A：参考文本与音频不匹配（最常见）

**症状**：首次合成就报错，或换了参考音频后开始报错。

**诊断**：
```bash
# 检查语速是否合理（正常 4-5 字/秒）
wsl -e bash -lc "source ~/cosyvoice-venv/bin/activate && \
  python /mnt/d/xiangmudaima/zjy14/probe/_verify_text_completeness.py"
```

**真实案例**：项目里 `1.6.mp3` 是 4.56 秒音频，但只标注了 11 个字
（应有约 19 字），导致合成报此错。

**解决**：
- 补全参考文本（人工听写最可靠）
- 或换一段文本已知的音频
- 引擎启动时会自动校验（`check_reference_consistency`），
  不通过的音色会被跳过并给出提示

#### 原因 B：参考文本缺 `<|endofprompt|>` 前缀

**症状**：音色注册成功、健康检查正常，但每次合成都失败。

**解决**：引擎的 `_load_voices_from_db` / `_load_voices_from_dir`
会自动补前缀。若手动调用上游 API，必须自己加：
```python
prompt_text = "You are a helpful assistant.<|endofprompt|>" + 正文
```

#### 原因 C：混合调用时保留了 `llm_prompt_speech_token`

**症状**：自定义 prompt_text 时出现。

**背景**：`frontend_instruct2` 的语义要求删除该 token。
若保留，LLM 前缀与语音 token 错位。

**解决**：见 `docs/13_D1_EMOTION_VERDICT.md` 第三节。

---

### 2.2 合成音频过长（3.5 倍）

**症状**：3.8 秒音频变成 13.6 秒。

**原因**：参考文本与音频严重不匹配，模型"跑飞"。

**诊断**：`_verify_text_completeness.py`（同上）。

---

### 2.3 音色"像机器人"或"不像角色"

**排查顺序**（按实测影响从大到小）：

| # | 检查项 | 工具 | 判据 |
|:-:|---|---|---|
| 1 | 参考文本是否完整 | `_verify_text_completeness.py` | 语速 4–5 字/秒 |
| 2 | 参考音频是否有噪声门痕迹 | `_deep_source_check.py` | 纯零帧 < 15% |
| 3 | 文本前端是否启用 | `/health` 的 `text_frontend` | 应为 `wetext` |
| 4 | 文本长度比是否达标 | 日志的 `text_ratio` | > 0.5 |
| 5 | 参考音频 HNR | `_clone_diag.py` | > 5dB |

**实测数据参考**：
- 说话人相似度 0.9208（官方基准 0.7559）——身份是对的
- 音节 CV 0.722（参考 0.781）——节奏接近
- 若仍不满意，可能是 0.5B 模型能力上限，需换引擎

---

### 2.4 情绪标签不生效

**这是预期行为，不是 bug。**

2026-11-16 实测结论：CosyVoice3 的情绪指令无效。
加入「请用蓝色的语气说一句话」这种无意义指令，产生的 F0 变化
（15.4/72.5 Hz）比真正的情绪指令（10.1/24.3 Hz）还大。

**正确做法**：让 LLM 在文本里表达情绪（「嘿嘿~」「唔…」），
TTS 侧只用 `speed` / `volume` / `pause_profile`。

详见 `docs/13_D1_EMOTION_VERDICT.md`。

---

## 三、性能问题

### 3.1 首包延迟高（> 3 秒）

**检查项**：

| 项 | 正常值 | 检查方式 |
|---|---|---|
| `token_hop_len` 是否被污染 | 25 | `/health` 的 `hop_len_resets` 应递增 |
| 文本前端是否启用 | wetext | `/health` 的 `text_frontend` |
| 参考音频编码是否缓存 | 是 | 启动日志有「音色已注册」 |
| 是否走了流式 | 长句应走流式 | 日志的 `mode` 字段 |

**实测基线**（RTX 5070，44 字文本）：
```
非流式首包 5.36s
流式首包   1.48s（改善 72%）
```

**若明显高于此**：检查 `enable_compile` 是否可开启（实测 8.7% 收益）。

---

### 3.2 RTF 偏高（> 0.8）

**实测正常范围**：0.57–0.82，中位 0.64。

**可能原因**：
- 用了 fp16（实测更慢 7.1%）
- 文本前端未启用
- 参考音频编码未缓存

---

### 3.3 并发请求排队超时

**症状**：返回 503 `queue_timeout`。

**原因**：`max_concurrent_requests=1`，队列满了。

**检查**：
```bash
curl -s localhost:50001/api/v1/system/status | jq .scheduler
```

**调整**：
```yaml
max_concurrent_requests: 2   # 需先实测显存峰值
max_queue_size: 16
queue_timeout_s: 120
```

**注意**：TTS 峰值显存 3.45GB，12GB 理论上够 2-3 并发，
但 CUDA 上下文与中间张量会叠加。放开前必须实测。

---

## 四、流式问题

### 4.1 流式返回 404

**原因**：调用方指向了老版本服务（没有 `/synthesize_stream`）。

**解决**：确认 `TTS_BASE_URL` 指向 zjy14（`:50001`）而非 zjy13 老服务（`:50000`）。

---

### 4.2 流式音频有咔哒声

**实测结论：本项目无此问题。** 三档文本的相邻样本最大跳变
0.383–0.524，超阈值样本 0 个。

**若出现**：检查是否有二次重采样（zjy14 输出 24kHz，与固件一致，不需要重采样）。

---

### 4.3 短句流式反而更慢

**这是预期行为。** 实测短句（10 字）流式 −4.4%。

**原因**：上游要攒够 28 token（约 1.12 秒音频）才出第一包。

**解决**：用 `StreamPolicy.should_stream()` 做长短句路由，阈值 15 字。

---

## 五、设备联调问题

### 5.1 板子不出声

**检查顺序**：

1. zjy14 服务是否在跑：`curl localhost:50001/health`
2. zjy13 的 `TTS_BASE_URL` 是否指向 `:50001`
3. 板子是否连上 zjy13 的 WebSocket（看 zjy13 日志）
4. 采样率是否一致（固件固定 24kHz，zjy14 输出 24kHz）

### 5.2 板子播放断续

**可能原因**：服务端下发太快，把链路推爆。

**检查**：背压是否生效。zjy13 的 `ws/server.py` 有 `lead_sec=0.8` 节流。

**背景**：zjy13 注释记录过"移动热点/WiFi Direct 链路上容易把下行推爆
（transport 写超时断连）"。

### 5.3 板子"更晚才出声"

**原因**：首块音频太小，固件攒不满 200ms 预缓冲。

**解决**：首突发至少 300ms（`FIRST_BURST_MS`）。
zjy14 的 `bridge/protocol.py` 已实现。

### 5.4 播放与录音冲突

**这是固件约束，不是 bug。**

`firmware/main/app_audio.h:15` 记录：
ES7210 与 ES8311 共用 I2S 总线，录音 16kHz 与播放 24kHz
不能同时工作，必须半双工切换。

---

## 六、数据问题

### 6.1 参考文本被改错

**症状**：合成报 Kernel size 错误，或音色明显不对。

**真实案例**：项目的 `verify_phase3.sh` 早期版本硬编码了「官方示例」文本
去恢复，把正确的爱莉希雅参考文本覆盖了。

**已修复**：脚本改为读原值再恢复。另有 `scripts/fix_elysia_transcript.sh`
可从原始 `.txt` 重新导入。

**预防**：测试脚本里的"恢复"操作，必须用读到的原值，不能凭记忆硬编码。

---

### 6.2 数据库里声音档案丢失

**检查**：
```bash
sqlite3 ~/zjy14-data/custom_voice.db "SELECT speaker_id, audio_path, length(transcript) FROM speakers;"
```

**重新导入**：
```bash
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/fix_elysia_transcript.sh
```

---

### 6.3 音频文件路径失效

**原因**：声音档案只记录路径，不复制原始文件。

**解决**：确认 `audio_path` 指向的文件仍存在；若移动了素材，用
`PATCH /api/v1/speakers/{id}` 更新路径。

---

## 七、前端问题

### 7.1 npm 报"禁止运行脚本"

**症状**：
```
npm : 无法加载文件 ...\npm.ps1，因为在此系统上禁止运行脚本
```

**解决**：用 `npm.cmd` 代替 `npm`：
```powershell
& "D:\New Folder\npm.cmd" run dev
```

---

### 7.2 前端页面空白 / API 请求失败

**检查**：
1. 后端是否在跑：`curl localhost:50001/health`
2. Vite proxy 是否指向正确端口（`web/vite.config.ts` 的 `ZJY14_BACKEND`）
3. 浏览器控制台是否有 CORS 错误（dev 用 proxy 规避，生产需反代）

---

### 7.3 构建报 `TS6310: Referenced project may not disable emit`

**原因**：`tsconfig.node.json` 里 `composite: true` 与 `noEmit: true` 冲突。

**已修复**：改为 `emitDeclarationOnly: true` + 指定 `outDir`。

---

## 八、环境问题

### 8.1 PowerShell 把源码改坏了

**症状**：`from` 变成 `rrom`，`def` 变成 `der`，`if` 变成 `ir`。

**原因**：PowerShell 的 `.Replace()` 把 `\f` 当成换页转义符。

**解决**：
```powershell
# 从 git 恢复
git checkout -- path/to/file.py
```

**教训**：不要用 PowerShell 做源码文本替换，用 `edit` 工具或 Python 脚本。

---

### 8.2 Python 文件有 BOM 导致 `SyntaxError: invalid non-printable character U+FEFF`

**原因**：PowerShell 的 `Set-Content -Encoding UTF8` 会写入 BOM。

**解决**：
```bash
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && python scripts/strip_bom.py"
```

**预防**：写文件用 `[System.IO.File]::WriteAllText($path, $content, (New-Object System.Text.UTF8Encoding($false)))`。

---

## 九、诊断工具索引

| 工具 | 用途 |
|---|---|
| `probe/_audit_config_usage.py` | 配置字段是否真被使用、未使用 import |
| `probe/_verify_text_completeness.py` | 参考文本与音频是否匹配 |
| `probe/_deep_source_check.py` | 素材质量（噪声门、说话人一致性） |
| `probe/_clone_diag.py` | 克隆质量（HNR、平坦度、质心） |
| `probe/_speaker_similarity.py` | 说话人相似度（campplus 余弦） |
| `probe/_p4_stream_test.py` | 流式收益实测 |
| `probe/_p7_stability.py` | 稳定性压测（显存泄漏、RTF 趋势） |
| `probe/_p5_e2e.py` | 设备协议端到端 |
| `scripts/verify_phase*.sh` | 各阶段验收 |

---

## 十、获取帮助

1. 先查本手册对应章节
2. 看日志：`tail -50 ~/zjy14-data/logs/zjy14.log`
3. 用 `/health` 和 `/api/v1/system/status` 获取运行时状态
4. 跑对应阶段的验收脚本，看哪一项失败

**日志字段说明**见 `docs/15_PHASE6_REPORT.md` 的日志中心章节。
