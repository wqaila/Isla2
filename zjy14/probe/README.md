# probe/ —— 可复现诊断脚本

本目录放**可复现的验证与诊断脚本**。每个脚本都是独立可跑的，
输出写到项目根下与脚本同名的目录（已在 `.gitignore` 中排除）。

> 命名约定：`_` 开头表示"诊断脚本"，`.gitignore` 里有 `!probe/_*.py` 白名单，
> 所以它们会被正常入库 —— 与仓库根目录的 `_*.py` 临时脚本不是一回事。

## 情绪控制审计（2026-11-20）

六个脚本，按**从浅到深**的顺序，回答一个核心问题：
**情绪控制是真的进了模型，还是只在业务层打了个标签？**

审计结论见 [`../docs/30_EMOTION_CONTROL_AUDIT.md`](../docs/30_EMOTION_CONTROL_AUDIT.md)。

### 前置条件

```bash
# TTS 服务在跑
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/start.sh cosyvoice3
```

除 `_audit_instruct2_usage.py` / `_audit_instruct2_spk_trap.py` /
`_audit_instruct_decisive.py` / `_audit_stream_instruct.py` 会**自己加载模型**
（需要独占显存，请先停掉服务），其余通过 HTTP 调用在跑的服务。

### 1. `_audit_emotion_basic.py` —— 六种情绪对照（入门）

```bash
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && \
  ~/cosyvoice-venv/bin/python probe/_audit_emotion_basic.py"
```

用同一文本、同一参考，只改 `emotion` / `instruction`，看输出是否不同。

**⚠️ 这个脚本的结论不能直接用。** 它只显示"有差异"，
但 `ras_sampling`（top_p=0.8, top_k=25）每次推理都有随机性，
同文本连跑两次本来就会不同。要判断差异是否真实，必须跑第 2 个脚本。

### 2. `_audit_emotion_signal_noise.py` —— 组间 vs 组内（**最严谨**）

```bash
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && \
  ~/cosyvoice-venv/bin/python probe/_audit_emotion_signal_noise.py"
```

每个情绪重复 3 次，分别算：

- **组内 σ**：同一情绪重复跑的噪声
- **组间 |Δ|**：不同情绪之间的差异

**判据**：`组间 / 组内` 比值。

| 比值 | 含义 |
|---|---|
| < 3 | 差异与噪声同量级 → **情绪没有进入模型** |
| > 3 | 差异显著 → 情绪可能生效，需听测确认 |

2026-11-20 实测：**0.96**（无效）。

### 3. `_audit_instruct2_usage.py` —— `instruct2` 怎么用（含 endofprompt 陷阱）

```bash
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && \
  ~/cosyvoice-venv/bin/python probe/_audit_instruct2_usage.py"
```

验证两件事：

1. `inference_instruct2` 是否可用
2. `<|endofprompt|>` 标记是否必需

**实测**：不带标记会直接断言失败：

```
AssertionError: <|endofprompt|> not detected in CosyVoice3 text or prompt_text
```

`cosyvoice/llm/llm.py:479` 有硬断言（token id 151646）。

### 4. `_audit_instruct2_spk_trap.py` —— `zero_shot_spk_id` 静默丢弃陷阱

```bash
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && \
  ~/cosyvoice-venv/bin/python probe/_audit_instruct2_spk_trap.py"
```

四组对照：

| 组 | 调用 | 结果 |
|---|---|---|
| A | `zero_shot` 无 spk_id + 参考文本 | 基线 |
| B | `instruct2` 无 spk_id + 悲伤指令 | 指令生效 |
| C | `instruct2` **有 spk_id** + 悲伤指令 | **指令被丢弃** |
| D | `zero_shot` 有 spk_id | 与 A 逐字节相同 |

**关键**：A 与 D 的 md5 完全相同 → `spk2info` 存的就是同一份 prompt。

### 5. `_audit_instruct_decisive.py` —— 决定性判据

```bash
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && \
  ~/cosyvoice-venv/bin/python probe/_audit_instruct_decisive.py"
```

**固定 spk_id，只换指令文本**，跑三条语义完全相反的指令：

| 指令 | md5 |
|---|---|
| 非常悲伤、低沉、想哭 | `cdd7a507f832` |
| 非常开心、高昂、兴奋 | `cdd7a507f832` |
| 愤怒、大声、咆哮 | `cdd7a507f832` |

**逐字节相同** → 指令被完全丢弃。这是最硬的证据。

同脚本还会跑「无 spk_id」对照组，证明模型**本身支持**情绪
（悲伤 17.6s/220Hz vs 开心 10.8s/338Hz）。

### 6. `_audit_stream_instruct.py` —— 流式路径也支持吗

```bash
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && \
  ~/cosyvoice-venv/bin/python probe/_audit_stream_instruct.py"
```

只验证非流式是不够的 —— 项目生产路径走的是流式。
实测流式下指令**同样生效**（时长差 6.84s）。

## 输出说明

每个脚本在项目根下写一个同名目录：

```
zjy14/_audit_emotion_basic/
zjy14/_audit_emotion_signal_noise/
...
```

内含 `.wav` / `.pcm` 音频与 `results.json`。这些目录**不入库**（见 `.gitignore`）。

## 其它脚本

| 脚本 | 用途 |
|---|---|
| `run_d1.sh` | 运行探针脚本并过滤上游噪声日志 |
| `run_device_e2e.sh` | **真机联调一键验收** |
| `_verify_zjy13_tts.py` | 用 zjy13 生产代码的 `TTSClient` 实打 |
| `_check_win_port.sh` | WSL → Windows 端口连通性探测 |
| `_p8_device_check.py` | 六项链路诊断（含真机在线判定） |
| `_audit_config_usage.py` | 配置项是否真的被消费 |
| `_audit_opt_switches.py` | 优化开关是否真的生效 |

## 一个通用坑：不要用 PowerShell 测二进制/中文接口

`Invoke-WebRequest -Body '{"text":"中文"}'` 会按系统 ANSI（GBK）编码发送，
服务端按 UTF-8 解析 → 中文变 `????` → 模型合成不出音频 → 报 500
「模型未返回音频」。

**这不是服务故障。** 用 Python 发 UTF-8 请求即可：

```python
body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(url, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"})
```

同理，`Invoke-WebRequest` 读二进制响应（如 `/synthesize_stream` 的
len32be 分块）也不可靠，会显示成 4 字节之类的假象。
