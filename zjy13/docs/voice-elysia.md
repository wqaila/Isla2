# 角色音色克隆：爱莉希雅

> 日期：2026-10-28
> 素材来源：`C:\Users\user\Desktop\aili`
> 引擎：CosyVoice3-0.5B（WSL2 / RTX 5070）
> 所有数据为真实执行结果。

---

## 一、素材情况

### 1.1 文件清单

| 文件 | 时长 | 内容 | 用途 |
| --- | --- | --- | --- |
| 1.0.mp3 | 9.43s | 早上好，新的一天从一场美妙的邂逅开始 | 候选 |
| 1.1.mp3 | 8.12s | 这么早起床是为了能早点见到我吗 | 候选 |
| **1.2.mp3** | **1280.5s（21分20秒）** | **角色语音合集** | **主素材** |
| 1.3.mp3 | 8.86s | 快去休息了，约好了我们在梦里相见 | 候选 |
| 1.4.mp3 | 7.76s | 这么晚了还不睡吗，是在想我对不对 | 候选 |
| 1.5.mp3 | 9.67s | 睡不着的话就来聊聊天吧 | 候选 |
| 1.6.mp3 | 9.06s | 晚上好，如此美妙的夜晚 | 候选 |
| 1.7.mp3 | 8.31s | 月亮升起来了，要陪我一起去看星星吗 | 候选 |

> 没有 `1.2` 之外的缺口：编号从 1.1 直接跳到 1.3 是原始素材就没有 1.2 的短片段，
> 1.2 后来补入的是长合集。

### 1.2 技术特征（实测）

| 项 | 值 |
| --- | --- |
| 采样率 | 24000 Hz |
| 声道 | 单声道 |
| 位深 | 16 bit |
| 背景音乐 | **无**（静音段为纯数字零，-180 dBFS） |
| 基频 | 约 268 Hz |
| 音量差异 | 1230s 后比前段高约 10 倍 |

**结论**：干净的干声，非常适合零样本克隆。

---

## 二、选段流程

### 2.1 工具

`tools/prepare_voice_ref.py`，两个子命令：

```powershell
# 扫描素材，列出高质量候选
python -m tools.prepare_voice_ref scan "C:\Users\user\Desktop\aili\1.2.mp3" --top 15

# 按区间导出参考音频
python -m tools.prepare_voice_ref pick "1.2.mp3" --start 174.89 --end 184.59 `
  --out server\characters\voices\elysia\ref_elysia.wav --text "参考文本"
```

### 2.2 打分逻辑

| 维度 | 权重 | 说明 |
| --- | --- | --- |
| 信噪比 | 60% | 30dB 封顶 |
| 时长贴近 8 秒 | 40% | 越接近理想值越高 |
| 削波惩罚 | 扣分 | 削波比例 × 100 |

**低于 20dB 直接淘汰**。

### 2.3 阈值踩坑（重要）

第一版用 `峰值 × 6%` 作阈值，只找到 **10 个候选**。

原因：这份素材前 20 分钟电平低、最后 1 分钟突然高 10 倍。
阈值被最响的几秒抬高，导致前面 90% 的正常语音被判成静音。

**修复**：改为相对噪声底 —— `max(噪声底 × 4, 峰值 × 0.5%)`。

修复后候选从 **10 个增加到 92 个**。

### 2.4 长音频的处理

21 分钟几乎连续说话（中位有声段仅 1.2 秒），没有长停顿。

第一版逻辑是"超长段截取开头 8 秒"，等于浪费 99% 的素材。

**修复**：在长块内**滑窗**，每 4 秒滑动一次，用窗口内**最低 20% 帧能量**打分
（而不是平均值——平均值高的窗口可能中间夹着长停顿），
再按重叠度去重，取前 8 个。

---

## 三、最终选定

| 项 | 值 |
| --- | --- |
| 区间 | 174.89s ~ 184.59s |
| 时长 | 9.70s |
| 信噪比 | 60.8 dB |
| 打分 | 0.915 |
| 导出格式 | 24kHz / 16bit / 单声道 |
| 归一化 | 峰值 -3 dBFS |

**参考文本**：

```
我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀
```

### 选择理由

1. **自然边界**：时长 9.70s 不是整数，说明是真实停顿切出的完整语句，
   而不是滑窗截断（滑窗截断会切断单词）
2. **不含专有名词**：ASR 对"爱莉希雅""伊甸""烛火之鹅"等词转写不稳，
   这段全是常用词，转写可靠
3. **内容合适**：完整表达角色性格，语气自然

### 文本对齐验证

导出后用 ASR **回读**导出的 WAV：

```
ASR 回读: 我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀
```

与参考文本**逐字一致**。零样本克隆对参考文本准确性极敏感，
这一步是必须的。

---

## 四、踩坑：`<|endofprompt|>` 前缀

### 现象

音色注册成功、健康检查正常、参考音频格式完全正确，
但**每次合成必然失败**：

```
RuntimeError: Calculated padded input size per channel: (3).
Kernel size: (4). Kernel size can't be greater than actual input size
```

### 定位过程

| 实验 | 结果 | 结论 |
| --- | --- | --- |
| elysia 音频 + elysia 文本 | ❌ 失败 | 基线 |
| elysia 音频 + 内置文本 | ✅ 成功 | **不是音频问题** |
| 内置音频 + elysia 文本 | ❌ 失败 | **是文本问题** |
| elysia 音频 + 空文本 | ❌ 失败 | 空文本也不行 |
| 换各种合成文本 | 全部失败 | 与合成文本无关 |

### 根因

CosyVoice3 的 `prompt_text` 必须是：

```
You are a helpful assistant.<|endofprompt|><参考音频对应的文本>
```

`<|endofprompt|>` 是**必需的分隔符**，用来分隔系统指令与参考语音文本。

缺前缀时 LLM 前缀错位，生成阶段拿到过短 token 序列（3 帧），
而卷积核需要 4 帧，于是崩溃。

官方所有示例（`example.py`、`vllm_example.py`）的 `prompt_text` 都带此前缀。

### 修复

服务端在扫描音色时**自动补前缀**：

```python
if "<|endofprompt|>" not in text:
    text = PROMPT_PREFIX + text
```

磁盘上的 `.txt` 保持纯文本，人工编辑时不用记特殊标记。

**验证**：加前缀后立刻成功合成 5.40s 音频。

---

## 五、多音色机制

### 5.1 目录约定

```
server/characters/voices/
└─ elysia/
   ├─ ref_elysia.wav     # 参考音频
   └─ ref_elysia.txt     # 同名文本
```

新增音色只需建子目录，重启服务自动注册。

### 5.2 注册实现

CosyVoice3 的 `add_zero_shot_spk(prompt_text, prompt_wav, spk_id)`
把编码结果存进 `spk2info` 字典，天然支持多音色共存：

```python
for voice_id, prof in runtime.voices.items():
    model.add_zero_shot_spk(prof.ref_text, str(prof.ref_wav), voice_id)
```

启动时一次性注册全部音色（实测 1.74s），切换音色无需重新编码。

### 5.3 当前可用音色

| voice_id | 来源 | 说明 |
| --- | --- | --- |
| `xiaoman` | CosyVoice 内置示例 | 兜底 |
| `elysia` | 角色语音素材克隆 | 当前默认 |

---

## 六、实测结果

### 6.1 合成质量

| 文本 | 音频时长 | 合成耗时 | RTF |
| --- | --- | --- | --- |
| 嗯哼~舰长，你好呀！今天也是元气满满的一天呢！ | 5.40s | 3.14s | 0.58 |
| 舰长今天辛苦了，要不要休息一下呢？人家会一直在这里陪着你的哦。 | 6.00s | 3.50s | 0.58 |

### 6.2 服务启动

| 阶段 | 耗时 |
| --- | --- |
| 模型加载 | 11.2s（首次含 wetext 下载时 51.9s） |
| 音色注册（2 个） | 1.74s |
| 预热 | 3.2~3.5s |

### 6.3 全链路（角色卡 + 音色）

```
角色  : 爱莉希雅 (v1.0)
音色  : elysia

用户: 你好
回复: 你好呀舰长~ ✨ 上午的时光最舒服了呢，有没有好好吃早饭呀？
音频: 5.40s / 3136ms / RTF 0.581

用户: 今天有点累
回复: 唔…累了呀。那就先别硬撑着，找个舒服的地方歇一歇嘛~ 人家在这儿陪着你，不说话也可以哦 🌸
音频: 8.84s / 5136ms / RTF 0.581

端到端均值: 5029 ms
```

角色语气（"舰长""人家""唔…""~""✨🌸"）与音色均已生效。

---

## 七、未验证项

- [ ] **主观听感**：合成音频是否真的像爱莉希雅（需人工试听）
- [ ] 与原始角色的相似度客观评分
- [ ] 长句（> 100 字）合成的稳定性
- [ ] 情绪变化时音色是否保持
- [ ] 多音色同时合成的显存占用
- [ ] 参考音频换成合并多段后效果是否更好

---

## 八、复现步骤

```powershell
cd D:\xiangmudaima\zjy13

# 1. 扫描素材（需要 .venv-asr：numpy + miniaudio + funasr）
.\.venv-asr\Scripts\python.exe -m tools.prepare_voice_ref scan `
  "C:\Users\user\Desktop\aili\1.2.mp3" --top 15 --json-out logs\voice_candidates.json

# 2. 导出选定段
.\.venv-asr\Scripts\python.exe -m tools.prepare_voice_ref pick `
  "C:\Users\user\Desktop\aili\1.2.mp3" --start 174.89 --end 184.59 `
  --out server\characters\voices\elysia\ref_elysia.wav `
  --text "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀"

# 3. 启动 TTS 服务（自动注册音色）
wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/serve_tts_wsl.sh

# 4. 验证
curl http://127.0.0.1:50000/health
.\.venv\Scripts\python.exe -m tools.dialogue_loop --text-mode
```

---

## 九、依赖说明

`tools/prepare_voice_ref.py` 需要：

| 包 | 用途 | 装在哪个环境 |
| --- | --- | --- |
| numpy | 音频数组运算 | `.venv` / `.venv-asr` |
| miniaudio | 解码 mp3（本机无 ffmpeg） | `.venv` / `.venv-asr` |
| funasr | 转写参考文本 | `.venv-asr` |

> 本机 `libsndfile` 加载失败（`error 0x7e`），`soundfile` 不可用，
> 所以 mp3 解码改用 `miniaudio`（自带解码器，无需系统库）。
