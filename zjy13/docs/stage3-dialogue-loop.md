# 阶段三验收：终端语音对话闭环

> 状态：**通过**。ASR + LLM + TTS 三段已串通，有真实计时数据。

---

## 一、链路架构

```
WAV 文件
  ↓  ASR（Windows .venv-asr / FunASR Paraformer / RTX 5070）
识别文本
  ↓  LLM（Windows .venv / DeepSeek 云端 API）
回复文本
  ↓  TTS（WSL2 / CosyVoice3 / HTTP 127.0.0.1:50000）
WAV 音频（24000 Hz / 16 bit / 单声道）
```

三段分处不同环境，职责清晰：

| 模块 | 环境 | 依赖 |
| --- | --- | --- |
| ASR | Windows `.venv-asr` | torch 2.8.0+cu128、FunASR 1.4.16 |
| LLM | Windows `.venv` | httpx、PyYAML |
| TTS | WSL2 `~/cosyvoice-venv` | torch 2.8.0+cu128、CosyVoice3 |

---

## 二、实测数据

### 完整音频链路（1 轮）

```
识别: 欢迎大家来体验达摩院推出的语音识别模型
回复: 嗯，听你这么说，像是要开场了。我能帮你做点什么？
音频: 4.80s / 2980ms 合成 / RTF 0.621
```

| 阶段 | 耗时 |
| --- | --- |
| ASR | 106 ms |
| LLM 首字 | 909 ms |
| LLM 完成 | 1142 ms |
| TTS | 2981 ms |
| **端到端** | **4228 ms** |

### 文本模式 3 轮对话

| 轮次 | ASR | LLM首字 | LLM完成 | TTS | 端到端 |
| --- | --- | --- | --- | --- | --- |
| 1 | 0 | 918 | 1072 | 2014 | 3087 |
| 2 | 0 | 778 | 943 | 1641 | 2584 |
| 3 | 0 | 867 | 1000 | 2023 | 3022 |

**端到端均值 2898 ms**，多轮记忆正常（第 3 轮正确回答"记得，你叫阿哲"）。

### TTS RTF 稳定性

| 场景 | RTF |
| --- | --- |
| 文本模式（2.88s 音频） | 0.699 |
| 文本模式（2.56s 音频） | 0.641 |
| 文本模式（3.04s 音频） | 0.665 |
| 完整链路（4.80s 音频） | 0.621 |

**稳定在 0.62~0.70**，符合性能测试预期（0.629）。

---

## 三、TTS 服务

### 启动

```powershell
wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/serve_tts_wsl.sh
```

服务启动时依次执行：

1. 加载模型（实测 9.2s）
2. **缓存参考音频编码**（0.49s，实测省 16.5% 合成耗时）
3. **预热两次**（3.42s，避免首句承担 CUDA kernel 初始化）

### 健康检查

```powershell
curl http://127.0.0.1:50000/health
```

```json
{
  "ok": true,
  "engine": "cosyvoice3",
  "sample_rate": 24000,
  "device": "cuda",
  "gpu": "NVIDIA GeForce RTX 5070",
  "arch_match": true,
  "load_s": 9.25,
  "warmup_s": 3.42,
  "prompt_cached": true
}
```

### 接口

`POST /synthesize`

```json
{"text": "你好", "voice_id": "xiaoman", "speed": 1.0}
```

返回 WAV 二进制，头部带元数据：

```
x-sample-rate: 24000
x-duration: 3.280
x-total-ms: 2268.7
x-rtf: 0.6917
x-engine: cosyvoice3
```

---

## 四、关键设计决策

### 1. TTS 不可达时明确报错，不静默降级

V2 第六节第 7 条要求：

> 本地 TTS 模型加载失败时，显示明确错误。除非用户明确配置备用云端 TTS，
> 否则不得偷偷回退到云端服务。

实现：`TTSUnavailableError` 抛出后，`dialogue_loop` 直接退出并提示启动命令，
不会尝试其他路径。有测试 `test_service_unavailable_raises` 锁定。

### 2. 只监听回环地址

V2 第六节第 10 条要求 TTS 端口只监听 127.0.0.1。

实现：`uvicorn.run(app, host="127.0.0.1", port=50000)`。
WSL2 的 localhost 自动转发到 Windows，无需额外配置。

### 3. 应用了两项实测优化

| 优化 | 效果 | 位置 |
| --- | --- | --- |
| 参考音频缓存 | -16.5% | `add_zero_shot_spk` |
| 重置 token_hop_len | 避免流式退化 | 每次合成前 |

### 4. fp16 明确不用

实测慢 7.1%、显存 +27%，代码里固定 `fp16=False` 并注释原因。

---

## 五、遇到的问题与解决

### 问题 1：`ModuleNotFoundError: No module named 'cosyvoice'`

**原因**：`os.chdir` 在 `import` 之前，但 `sys.path` 未包含仓库根目录。

**解决**：先 `sys.path.insert(0, repo)` 再 `chdir`。

### 问题 2：PowerShell 传 JSON 报 422

**现象**：

```json
{"detail":[{"type":"json_invalid","msg":"JSON decode error"}]}
```

**原因**：PowerShell 的 `-Body` 会把中文和引号转义搞坏。

**解决**：用 `curl.exe --data-binary @文件` 传参。这是**测试工具的问题，不是服务的 bug**。

### 问题 3：`.venv-asr` 缺 httpx

**原因**：完整链路要同时用 ASR（numpy/torch）和 LLM/TTS（httpx），两个环境依赖不重叠。

**解决**：给 `.venv-asr` 补装 httpx、python-dotenv 等。
**建议**：后续可以把完整链路统一到 `.venv-asr`（它已有 torch，再加 LLM 依赖即可）。

### 问题 4：pytest 临时目录混入 git

**原因**：Windows 上 pytest 的 `tmp_path` 落在项目目录内。

**解决**：`.gitignore` 加 `pytest-of-*/`。

---

## 六、运行方式

### 前置：启动 TTS 服务（单独终端）

```powershell
wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/serve_tts_wsl.sh
```

### 完整音频链路

```powershell
cd D:\xiangmudaima\zjy13
$env:PYTHONUTF8="1"
.\.venv-asr\Scripts\python.exe -m tools.dialogue_loop "tests\audio\asr_example.wav"
```

### 文本模式（跳过 ASR）

```powershell
.\.venv\Scripts\python.exe -m tools.dialogue_loop --text-mode
```

### 测试

```powershell
# LLM + TTS 侧（.venv）
.\.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_audio_utils.py --ignore=tests/test_asr_engine.py

# ASR 侧（.venv-asr）
.\.venv-asr\Scripts\python.exe -m pytest tests/test_audio_utils.py tests/test_asr_engine.py -q
```

---

## 七、延迟分析

端到端 4228 ms 的构成：

| 阶段 | 耗时 | 占比 |
| --- | --- | --- |
| ASR | 106 ms | 2.5% |
| LLM | 1142 ms | 27.0% |
| **TTS** | **2981 ms** | **70.5%** |

**TTS 是绝对瓶颈**，与性能剖析结论一致（LLM 占 TTS 内部 79%）。

### 可优化空间

| 优化 | 预期收益 | 复杂度 |
| --- | --- | --- |
| 流式 TTS | 首包 2.98s → 1.74s | 中 |
| vLLM 加速 | LLM 部分 2-3 倍 | 高 |
| 更短回复 | 线性减少 TTS 时间 | 低（角色卡已限制） |

**当前 4.2 秒在可接受范围**，但用户会明显感到等待。

---

## 八、未验证项

- [ ] **真实麦克风录音**：只用了官方示例 WAV
- [ ] **ESP32 采集的音频格式**：开发板实际输出未测
- [ ] **音频播放**：生成的 WAV 未在开发板或扬声器上播放验证
- [ ] **ASR + TTS 同时驻留显存**：目前分时使用（ASR 加载时 TTS 在 WSL 里也占显存）
  - TTS 3.57 GB + ASR 约 2 GB ≈ 5.6 GB，12 GB 够用但未实测并发
- [ ] **连续 10 轮稳定性**：只测了 3 轮
- [ ] **音色主观质量**：需要你实际听
- [ ] **打断/取消**：未实现

---

## 九、下一步

按 V2 阶段四，接下来是 **WebSocket 通信**：

1. 定义统一消息协议（V2 第十一节）
2. 实现客户端模拟器
3. 测试音频上传、文字返回、音频下载
4. 测试重复消息、超时、取消、连接中断

前置条件：需要先确定开发板的音频采集格式（V2 第五节要求核对硬件）。
