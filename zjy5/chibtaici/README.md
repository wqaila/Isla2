# 视频台词识别工具（chibtaici）

从视频里把**台词**抽出来，输出 **去重纯文本**（喂给语料流程）、**SRT 字幕**（保留时间轴），
或 **jsonl**（逐句带溯源：BV 号 + 时间点 + 通道 + 置信度）。

它是 `zjy5` 下的第二个工具，和 [`bilibili_downloader`](../bilibili_downloader/)
是**两件独立的事** —— 那个负责下载，这个负责识别。

> **先走字幕那条路**：如果视频本身带 CC / AI 字幕，
> 用 `bilibili_downloader --harvest` 直接抄文本，又快又准，根本不用跑这里的 OCR / 语音识别。
> 这个工具负责**兜底**「没字幕」的视频。

---

## 两条识别路线

工具同时走两条路，**可以单独启用，也可以一起用**：

| 路线 | 用什么 | 适合 |
|------|--------|------|
| **OCR** | PaddleOCR，读画面下方字幕区域 | 视频**自带硬字幕** |
| **语音识别** | whisper / faster-whisper | 视频**没有字幕**，或字幕不完整 |

两条路的结果会**按时间合并**：判为「同一条台词」的并起来（文本取更长的那份），
文本不同的**各自成条、不再拼接**（详见下方「合并策略」）。

---

## 安装

依赖比较重（GPU 版 PaddleOCR + PyTorch），首次安装耗时较长：

```bash
cd zjy5/chibtaici
pip install -r requirements.txt
```

`requirements.txt` 里几个**刻意钉住的版本**不是随手写的，改动前请先看注释：

| 包 | 版本 | 为什么钉住 |
|----|------|-----------|
| `opencv-python` | 4.6.0.66 | 老版本，与 `numpy==1.24.3` 配套。**注意**：这个理由原本写的是「兼容 PaddleOCR 2.7.0」，paddleocr 升到 3.4.0 后没再复测，属于保守保留 —— 想升级请先跑 `tests/smoke_ocr.py` |
| `paddlepaddle-gpu` | 3.4.0 | RTX 50 系（`sm_120`）**必须** CUDA 12.8+ 的 paddle，2.6.2 装了也只能跑 CPU，见下「GPU 环境」 |
| `paddleocr` | 3.4.0 | 与 paddle 3.4.0 配套。代码同时兼容 2.x / 3.x 的返回结构，所以两边都能装 |
| `torch` / `torchvision` | 2.1.0+cu118 | 走 `--extra-index-url` 装 CUDA 11.8 版。**它没有 sm_120 内核**，但在 RTX 50 系上**仍能跑通**（靠 PTX JIT 兜底，见下） |

> ⚠️ **torch 2.1.0+cu118 在 RTX 5070 上的实测结论（2026-09-30）**：
> 导入时会警告 `sm_120 is not compatible with the current PyTorch installation`，
> 但 **Linear / Conv1d / LayerNorm / Softmax / fp16 矩阵乘全部实测通过** ——
> 因为该构建内嵌了 `compute_37` 的 PTX，驱动会 JIT 到 sm_120。
> 也就是说**能用，但走的是兜底路径**（无原生内核，性能不是最优）。
> 判断这类问题一律**真跑一次运算**，不要只看 `torch.cuda.is_available()`
> （它返回 `True` 只说明驱动认到卡，不代表内核存在）。
>
> 另外 `faster-whisper` 走的是 **CTranslate2，不依赖 torch** ——
> 所以在 RTX 50 系上它比 openai-whisper 更稳也更快。两个后端都装时
> 默认会挑 openai-whisper（尊重原有默认），想强制走 faster-whisper 加
> `--use_faster_whisper`。

> **代码同时兼容 PaddleOCR 2.x 和 3.x**（2026-09-28 修复）。
> 两者的返回结构**完全不同**：2.x 是 `[[ [框, (文本, 置信度)], ... ]]`，
> 3.x 是 `[OCRResult]`（dict 风格，文本在 `rec_texts` 里）。
> `_extract_texts_from_ocr_result()` 负责抹平这个差异。
>
> 之所以要兼容而不是死钉 2.7.0：实际环境里装成 3.x 太常见了，
> 而按 2.x 解析会在 3.x 上抛异常 → 被上层吞掉 → **OCR 静默返回空**
> （表现为「跑完了但一句台词都没有」，完全没有任何报错，极难排查）。
> 现在这种失败会**逐条打印前 3 次 + 结束时汇总**，不再无声。
>
> ⚠️ 注意 3.x 默认拉的是 **PP-OCRv5 server 系列模型**（比 2.x 的 mobile 重），
> 纯 CPU 跑会明显更慢。介意的话可以降到 2.7.0。

> 没有 NVIDIA 显卡也能跑：把 `paddlepaddle-gpu` 换成 `paddlepaddle`（CPU 版），
> 并在命令行加 `--device cpu`。只是会慢很多。

另外 `opencc-python-reimplemented` 用来把识别结果**统一转成简体**。

---

## GPU 环境（RTX 50 系必看）

**RTX 50 系是 Blackwell（`sm_120`），对 CUDA 版本有硬要求**，踩过两次坑，记在这里：

| 坑 | 现象 | 解法 |
|----|------|------|
| **paddle 太老** | `paddlepaddle-gpu 2.6.2`（PyPI 上 Windows 最新）没有 sm_120 内核，装了也只能跑 CPU | 从官方源装 `3.4.0`（cu129），见下 |
| **wheel 不带 CUDA 运行时** | 装完报 `cublas64_12.dll is not configured correctly (error code 126)` | 把 cuBLAS/cuDNN 等 DLL 放进 `paddle/libs/`，见下 |

### 安装步骤

```bash
# 1. 从官方源装 GPU 版（PyPI 上没有 Windows 的 3.x）
#    ⚠️ 这个索引 URL 被百度 WAF 拦，curl 拿不到，但 pip 能跟到真实 CDN，正常用
pip install paddlepaddle-gpu==3.4.0 \
    --index-url https://www.paddlepaddle.org.cn/packages/stable/cu129/

# 2. 补 CUDA 运行时 DLL（wheel 里没打包）
#    如果本机别的环境装过 CUDA 版 torch，直接从它的 torch/lib 复制最省事
#    （torch 的 wheel 是自带完整 CUDA 运行时的）
```

第 2 步要放进 `paddle/libs/` 的文件：

```
cublas64_12.dll   cublasLt64_12.dll   cudart64_12.dll
cudnn64_9.dll     cudnn_adv64_9.dll   cudnn_cnn64_9.dll
cudnn_ops64_9.dll cudnn_graph64_9.dll cudnn_heuristic64_9.dll
cudnn_engines_precompiled64_9.dll     cudnn_engines_runtime_compiled64_9.dll
cufft64_11.dll    curand64_10.dll     cusolver64_11.dll
cusparse64_12.dll nvrtc64_120_0.dll   nvJitLink_120_0.dll
```

> ⚠️ **`nvJitLink_120_0.dll` 千万别漏**。`cusolver64_11.dll` 依赖它，
> 而 paddle 启动时就会加载 cusolver —— 少了它直接
> `OSError: [WinError 126] ... Error loading "…\libs\cusolver64_11.dll"`，
> **报错文件名会误导你去查 cusolver，其实 cusolver 本身是好的**。
>
> 复制完请跑一次自检（能全 OK 才算齐）：
>
> ```bash
> python -c "
> import ctypes, os, paddle
> libs = os.path.join(os.path.dirname(paddle.__file__), 'libs')
> for n in ['cublas64_12.dll','cudart64_12.dll','cusolver64_11.dll']:
>     ctypes.WinDLL(os.path.join(libs, n)); print('OK', n)
> "
> ```
>
> 如果报的正是 `cusolver64_11.dll`，先怀疑 `nvJitLink_120_0.dll` 缺失。

> 更规范的做法是 `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 …` 再复制，
> 但要多下 1 GB 多；从已有的 torch 里复制零下载，效果一样。
> 本机现成来源：`zjy7/lora_env/Lib/site-packages/torch/lib/`（55 个文件，含上述全部）。

### 怎么确认真的在用 GPU

```bash
python -c "
import paddle as p
print('device:', p.device.get_device())
a = p.ones([1024, 1024]); print('matmul sum:', float(p.matmul(a, a).sum()))
"
# device: gpu:0  且 matmul sum: 1073741824.0  才是真的在用 GPU
```

> **别只看 `paddle.device.is_compiled_with_cuda()`** —— 它返回 `True` 只说明
> 「编译时带了 CUDA」，不代表运行时能找到 CUDA 库、更不代表能真跑。
> 必须**实际跑一次运算**。只看 `get_default_device()` 也不够 ——
> 它只报告「打算用哪块设备」，**缺 DLL 时它照样输出 `gpu:0`**，
> 真正的错误要等第一次运算才抛出来。
>
> PaddleOCR 3.x 会自己调 `get_default_device()` 选设备，所以只要 `device: gpu:0`
> 且运算能跑通，OCR 就自动走 GPU，代码不用改。

---

## 用法

```bash
# 最常用：完整跑一遍，输出去重纯文本
python main.py --video 你的视频.mp4

# 建议加上：掐掉开头结尾各 90 秒（OP/ED、广告），标注来源
python main.py --video 番剧.mp4 --skip-head 90 --skip-tail 90 --video-id BV1xx411c7mD

# 想要能回溯的产物
python main.py --video 番剧.mp4 --format all   # txt + srt + jsonl
```

输出默认写到 `role_lines.txt`（`.srt` / `.jsonl` 由它推导同名文件）。

### 参数

**识别相关**

| 参数 | 默认 | 说明 |
|------|------|------|
| `--video` | **必填** | 视频文件路径 |
| `--output` | `role_lines.txt` | 输出文件（`.srt` / `.jsonl` 由它推导） |
| `--format` | `txt` | `txt`=去重纯文本、`srt`=带时间轴字幕、`jsonl`=句级结构化、`both`=txt+srt、`all`=三者都要 |
| `--whisper_model` | `large-v3` | 见下方「模型选型」。`tiny`/`base`/`small`/`medium`/`large`/`large-v2`/`large-v3`/`distil-large-v3` |
| `--device` | `auto` | `auto`/`cpu`/`cuda`，`auto` 会优先用 CUDA |
| `--hotwords` | 角色相关专有名词 | 注入 whisper 的 `initial_prompt`，**明显提升人名准确率**；传空字符串关闭 |
| `--no-vad` | 关（即 VAD 默认开） | VAD 切句只在 faster-whisper 上生效 |
| `--ocr_region` | `0,0.7,1,0.95` | OCR 区域，见下 |
| `--ocr_interval` | `5` | 每多少帧做一次 OCR（越大越快，但可能漏字） |
| `--no-ocr-skip` | 关（即跳帧默认开） | 关闭「字幕没变化就跳过 OCR」 |
| `--use_faster_whisper` | 关 | **强制**用 faster-whisper；不传则自动挑已安装的后端 |
| `--disable_ocr` | 关 | 只用语音识别 |
| `--disable_voice` | 关 | 只用 OCR |
| `--interactive_ocr` | 关 | 交互式设置 OCR 区域 |

**采集质量**（见下方「采集质量红线」）

| 参数 | 默认 | 说明 |
|------|------|------|
| `--video-id` | 从文件名推断 BV 号 | 写进 jsonl 的溯源字段 |
| `--no-clean` | 关（即清洗默认开） | 关闭规范化与质量过滤，保留原始的合并结果 |
| `--min-len` / `--max-len` | `4` / `100` | 台词长度红线（字数），超长的按标点切开 |
| `--require-complete` | 关 | 要求标点收尾；**默认关**：ASR 输出常整段没有句末标点，开了会一次损失大量语料 |
| `--skip-head` / `--skip-tail` | `0` / `0` | 掐掉开头/结尾多少秒，用来躲 OP/ED 与广告；可以填 `90` |

**说话人过滤**（见下方「语气打标」）

| 参数 | 默认 | 说明 |
|------|------|------|
| `--speaker-filter` | 关 | 启用本地 LLM 语气打标，低分剔除 |
| `--speaker-role` | `爱莉希雅` | 判定的目标角色 |
| `--speaker-model` | `qwen3:8b` | Ollama 模型名 |
| `--speaker-threshold` | `3` | 低于这个分剔除（1-5） |
| `--speaker-batch` | `20` | 一次请求打标多少句 |
| `--ollama-url` | `http://127.0.0.1:11434` | 本地 Ollama 地址 |

### 只走一条路的例子

```bash
# 视频有硬字幕，不需要语音识别（快很多）
python main.py --video 番剧.mp4 --disable_voice

# 视频没字幕，只做语音识别（默认就是 large-v3 + VAD）
python main.py --video 录屏.mp4 --disable_ocr

# CPU 上跑：默认的 large-v3 会非常慢，退回 medium
python main.py --video 录屏.mp4 --disable_ocr --device cpu --whisper_model medium
```

---

### 语音识别用哪个后端：自动挑

代码支持两个后端：`openai-whisper` 和 `faster-whisper`。
`requirements.txt` 装的是 **faster-whisper**（更快更轻）。

**不传 `--use_faster_whisper` 时会自动挑一个已安装的**：

| 已安装 | 未指定参数时选谁 |
|--------|-----------------|
| 只有 faster-whisper（默认安装结果） | faster-whisper（会打印一行提示） |
| 只有 openai-whisper | openai-whisper |
| 两个都有 | openai-whisper（尊重原有默认） |
| 两个都没有 | 抛清晰的错误，并告诉你怎么装 |

所以**装完 requirements 直接跑就行**，不用记着加参数。

> 这个自动挑选是 2026-09-24 补的。在此之前 `use_faster_whisper` 默认 `False`，
> 于是「按文档装完直接跑」必然报「openai-whisper 未安装」——
> 装的和默认要的对不上。

---

## 模型选型：别再用 base 了

默认模型在 2026-09-28 从 `base` 换成了 **`large-v3`**。

原因很直接：`base` 的中文错误率很高，跑出来的台词经常是「人名错、用词错」，
而这类错误**下游没法修** —— 语料错了，训练出来的模型就跟着错。
GPU 现在能正常跑（RTX 5070 + cu128），继续用 base 属于纯粹的白吃亏。

配套开了两件事：

| 开关 | 作用 |
|------|------|
| **VAD 切句**（faster-whisper，默认开） | silero VAD 先切出有人声的片段再识别，减少「静音段幻觉」和整段沉默；用 `--no-vad` 关 |
| **`--hotwords` 专有名词**（默认给了一组角色相关词） | 注入 whisper 的 `initial_prompt`。人名是最容易错的一类，把「爱莉希雅、芽衣、朔愿、律者、往世乐土」这类词提前告诉它，命中率会明显上去 |

> `openai-whisper` **不支持 VAD 参数**，这条只在 faster-whisper 上生效；
> 但 `initial_prompt` 两个后端都支持。

> ⚠️ **CPU 上跑 large-v3 会非常慢**。命令行加了 `--device cpu` 时程序会提示；
> 真要在 CPU 上跑，退回 `--whisper_model medium`。

---

## OCR 区域怎么设

`--ocr_region` 是 `x1,y1,x2,y2`，**同时支持相对坐标和绝对坐标**：

```bash
# 相对坐标（0~1）：默认值就是画面下方 70%~95% 那条字幕带
--ocr_region 0,0.7,1,0.95

# 绝对像素坐标：只要有任何值 > 1，就会自动按视频分辨率换算
--ocr_region 0,760,1920,1030
```

三种设置方式：

1. **直接传相对坐标** —— 知道字幕位置时最快
2. **传绝对像素** —— 会自动读取视频分辨率做换算
3. **交互式设置** —— 加 `--interactive_ocr`，按提示输入

另外目录里有一个 **`ocr_region_selector.html`**：在浏览器里打开，可以**可视化地
框选**字幕区域，比手算坐标直观得多。适合字幕位置不标准的视频。

---

## OCR 跳帧（默认开）

字幕**静止**的时间在视频里占大头，而静止意味着像素没变 —— 那就没必要每帧都调一次 OCR。

做法：把 OCR 区域缩成 32×8 的灰度缩略图算指纹，指纹和上一帧相同就**沿用上一帧的文字**，
不调用 OCR。用缩略图而不是原图，是为了让压缩噪点、轻微抖动不影响判断。

实测（合成视频，字幕静止约 4 秒）：

```text
跳帧生效：42/48 帧（88%）因字幕未变化而跳过 OCR 调用
```

顺带一个副作用是好的：条目现在有**真实的持续时间**了。
旧实现里 OCR 条目只有「命中某一帧」的时间点（`start == end`），SRT 只能靠补 1.2 秒蒙一个时长。

用 `--no-ocr-skip` 关掉它。

---

## 合并策略：相同文本才合并

`merge_timeline`（OCR 与语音两路结果合并）在 2026-09-28 改了规则：

| 情况 | 现在怎么做 | 旧做法 |
|------|-----------|--------|
| 文本相同 / 相似度 ≥ 0.85 / 互为包含 | 判为**同一条**，合并时间，文本取更长的那份，通道标 `voice+ocr` | 拼接 → 「同一句话 同一句话」 |
| 文本不同 | **各自成条** | 1.0s 内一律拼接 → 「上一句 下一句」黏成一条连行 |

旧规则会产生两类脏数据，都属于采集质量红线里明确要避免的：

1. **连行**：两条不同台词黏成一条，会教模型「一句话可以说很长」
2. **重复**：OCR 与 ASR 各识别一遍同一句，拼出「悲剧并非终结。 悲剧并非终结。」

> 这个改动会**改变 txt 的内容**（以前被合并的条目，现在会分成两条）。
> 如果你要的是旧行为，用 `--no-clean` 只能关掉文本过滤、关不掉合并规则 ——
> 合并规则是刻意改的，不再提供开关。

---

## 输出

### `--format txt`（默认）：纯文本，一行一句

```text
你今天怎么来了？
真的吗？
我才不会答应你。
```

**不含时间戳，且会去重**——适合直接喂给后续的语料处理流程（比如 `zjy7` 的 LoRA 训练数据）。

> 写文件之前会先做**规范化与质量过滤**（见「采集质量红线」）。
> 要未处理的原始结果，加 `--no-clean`（注意那也关掉了繁简/全半角统一）。

### `--format srt`：带时间轴的字幕

```srt
1
00:00:01,000 --> 00:00:02,200
你今天怎么来了？

2
00:00:03,000 --> 00:00:04,500
真的吗？
```

输出到与 `--output` 同名的 `.srt` 文件（`role_lines.txt` → `role_lines.srt`）。

**保留时间轴、不做文本去重**——同一句台词在不同时间出现会各占一条。

> 两条路的取舍：`txt` 是**语料**（要的是去重后的句子集合），`srt` 是**字幕**
> （要的是时间对齐）。所以两者的去重策略不同，不是同一个文件换个扩展名。
>
> OCR 条目现在有真实持续时间（靠跳帧知道字幕停留了多久）；万一仍是
> `start == end`，转 SRT 时会补一个最小持续时间（1.2 秒），并把结束时间夹在
> 下一条开始之前。两条字幕**开始时间相同**时，后一条会顺延到前一条之后 ——
> 字幕时间必须严格递增，否则播放器会闪烁。

### `--format jsonl`：句级结构化（下游最该拿的产物）

```json
{"text": "悲剧并非终结，而是希望的起始。", "video": "BV1xx411c7mD", "start": 123.4, "end": 126.8, "channel": "voice+ocr", "confidence": 0.93, "source": "voice"}
```

| 字段 | 含义 |
|------|------|
| `text` | 规范化后的台词 |
| `video` | BV 号（`--video-id`，默认从文件名推断） |
| `start` / `end` | 时间点（秒，保留 3 位） |
| `channel` | `subtitle` / `voice` / `ocr` / `voice+ocr`（两路都识别到同一句） |
| `confidence` | 置信度：ASR 是 `exp(avg_logprob)`，OCR 是该帧各行的平均分；**拿不到就写 `null`**，不编数字 |
| `source` | 原始通道（与 `channel` 区分：`channel` 可能被合并成 `voice+ocr`） |
| `speaker_score` | 语气打标分（只有开了 `--speaker-filter` 才有） |

**为什么要有 jsonl**：纯 txt 是**不可逆**的 —— 时间轴丢了、来源丢了，
之后想过滤、想按置信度加权、想回溯到出问题的那一帧，都做不了。
jsonl 相当于采集端的「责任交接单」，下游（比如 `zjy7` 的 `collect_corpus`）
直接吃它就行。

> txt 的去重**不丢信息**：重复次数会在终端打印出来（高频台词往往是口头禅，
> 是应该加权采样的信号，不是噪声）。要精确频次就用 jsonl 自己统计 ——
> `text_clean.text_frequencies()` 已经写好了一个。

---

## 采集质量红线

采集端的四条硬要求，实现在 `text_clean.py`（**只依赖标准库**，所以能单独测）。

| 维度 | 要求 | 怎么做的 |
|------|------|---------|
| **说话人** | 只要角色本人台词 | `--speaker-filter` 本地 LLM 语气打标（见下） |
| **长度** | 4~100 字 | `< --min-len` 丢弃；`> --max-len` **按标点切开**（连行会教模型「一句话可以说很长」） |
| **完整性** | 标点收尾的完整句 | `--require-complete` 可选；**默认关**，因为 ASR 输出常整段没标点，默认丢弃会一次损失大量语料 |
| **重复** | 跨视频去重但记频次 | txt 去重 + 打印频次 Top；jsonl 逐句保留，频次可随时算回来 |
| **规范化** | 繁→简、全半角统一、去表情残留 | 自动：OpenCC 繁→简、全角→半角、去零宽/emoji/音符记号、压缩重复标点（`……` 保留两个） |
| **噪声源** | OP/ED 歌词、广告、误识别的 BGM | `--skip-head` / `--skip-tail` 掐头去尾；`（BGM）` 这类标注会被去掉 |
| **溯源** | BV 号 + 时间点 + 通道 + 置信度 | jsonl 的字段，句级 |

运行时会打印一份**体检报告**，丢了多少、为什么丢，一眼可见：

```text
文本质量过滤：
  保留 812 条 / 检查 903 条
  丢弃 46 条：过短（< 下限）
  丢弃 45 条：规范化后为空
```

> 台词只是「她说什么」。训练一个角色模型还需要人设、user 侧提问、知识库、负样本、
> 通用数据、多轮样本 —— 完整清单和**采集端能贡献哪几项**见
> [`../台词之外还需要什么.md`](../台词之外还需要什么.md)。

> `--skip-head / --skip-tail` **默认是 0**，也就是不主动丢内容。
> 把它们做成默认 90 秒会静默丢掉片头片尾的台词，而且一旦落盘就找不回来；
> 需要时显式加上更稳妥：`--skip-head 90 --skip-tail 90`。

---

## 语气打标（轻量「说话人分离」）

现在分不出哪句是角色本人说的 —— 其他角色、旁白、歌词全混在一起，
**这是语料纯度最大的缺口**（人设污染比数据少更糟）。

重型方案（pyannote 那类说话人日志聚类）准但重，先不上。轻量方案是：
让本地 LLM 批量判断「这句话像不像角色本人的语气」，1~5 分，低分剔除。

```bash
python main.py --video 番剧.mp4 --format jsonl \
    --speaker-filter --speaker-role 爱莉希雅 --speaker-threshold 3
```

- 默认走本地 Ollama（`--ollama-url`，默认 `http://127.0.0.1:11434`），模型 `--speaker-model`（默认 `qwen3.5:9b-gguf`）
- 几千句也就十几分钟（按 `--speaker-batch` 20 句一批）
- **Ollama 没开 / 模型回复解析不了 / 请求失败时，程序不会丢数据**：
  提示一句、跳过打分、句子全部保留。宁可多留几条，也不静默删数据
- 分数会写进 jsonl 的 `speaker_score`，判错了还能事后调阈值重筛

> ⚠️ 访问本机 Ollama **必须绕开系统代理**，否则代理会把 `127.0.0.1` 的请求也接走，
> 表现为莫名其妙的 502。代码里已经用空 `ProxyHandler` 处理了。

> ⚠️⚠️ **默认模型必须指向本机确实装了的模型**。
> 原来写的是 `qwen3:8b`，但本机从未安装 —— 而本模块的失败模式是
> 「**静默跳过打分、不报错**」（设计如此），所以**跑完完全看不出问题**，
> 只会发现结果里没有 `speaker_score`。已改成 `qwen3.5:9b-gguf`。
> 换模型前先用 `check_available()` 探一下。

> ⚠️ **必须传 `think: false`**（已加）。Qwen3.5 默认开思考模式，实测
> 20 句一批要 **27 秒**（本该 2.9 秒），而且思考文本混进输出会让 JSON 解析失败。
> **prompt 里加 `/no_think` 是无效的**，只有 API 参数管用。

### 给**已落盘**的语料补打标：`tag_speakers.py`

`main.py` 的打标是采集流水线内部的一步，需要重跑 OCR。
但源视频往往已经删了 —— 这时用 `zjy5/tag_speakers.py` 直接给**现有的 txt** 补打标：

```bash
python tag_speakers.py --dry-run           # 只看要处理多少句
python tag_speakers.py --limit 300         # 小批量验证
python tag_speakers.py                     # 全量
python tag_speakers.py --filtered-dir out  # 额外输出过滤后的 txt
```

- 输出 `speaker_tags/<原名>.jsonl`（每行 `{"text","speaker_score","file"}`）
- 会先把超长行按标点切开再打分（否则「整屏连行 blob」会被当成一句打一个分，毫无意义）
- 最后打印**分数分布**和「像 / 不像」的条数，一眼看出语料纯度

**实测结果（6108 句，15.4 分钟，0 失败）**：

| 分数 | 句数 |
|------|------|
| 1 分 | 757 |
| 2 分 | 683 |
| 3 分 | 5 |
| 4 分 | 32 |
| **5 分** | **4631** |

→ **像角色本人 4668（76.4%）/ 不像 1440（23.6%）**。
被剔的是其他角色的剧情台词、旁白、游戏 UI。

> ⚠️ **有误杀**。抽样看到这些被判「不像」但其实很像角色说的：
> `怎么样,我是不是更漂亮了呀?`、`啊,凯文这习惯真该改改了`。
> 想更保守就把 `zjy7/train_config.json` 的 `data.min_speaker_score`
> 从 **3 调到 2**（只剔 1 分），**不用重跑打标**。

**下游怎么用**：`zjy7/prepare_data.py` 会读 `speaker_tags/*.jsonl`，
按 `data.min_speaker_score` **在切分后的片段级别**过滤
（超长 blob 行里好句坏句混在一起，整行丢会把好的也丢掉）。

---

## OCR 术语校正（`term_fix.py`）

实测 OCR 把角色名识别错得很厉害，而且**下游很难修**（下游只看到「芽依」，
不知道正确的是「芽衣」，只能猜）。采集端有原图和时间点，才是修的地方。

| 正确 | 次数 | 错写 | 次数 |
|------|------|------|------|
| 芽衣 | 771 | 芽依 | 43 |
| 读心术 | 6 | **独心术** | **7** ← 错的多过对的 |
| 千劫 | 77 | 千吉 / 千杰 | 20 / 48 |
| 伊甸 | 103 | 依电 | 9 |
| 梅比乌斯 | — | 美比乌斯 | 12 |

```bash
python -m chibtaici.term_fix            # 审计语料里的错写
python -m chibtaici.term_fix <目录>      # 指定目录
```

- `fix_terms(text)` —— 按 `KNOWN_ERRORS` 做替换，**只改实测确认过的**
- `find_suspicious(texts)` —— **只报告**疑似错写，供人工确认后加进表里
- `audit_corpus(texts)` —— 打印已确认错写的次数 + 疑似清单

> ⚠️ **刻意不做自动模糊纠正**（编辑距离 ≤1 就改）。实测那样会产生**大量误报**：
> `或者→律者`、`成长/很长/擅长→舰长`、`千万→千劫`、`英雄/英杰→英桀`……
> 分不清就会把**正确文本改坏**，那比不改更糟。
> 所以流程是「**报告 → 人工确认 → 加进表**」，不是「自动猜」。

---

## 进度显示

OCR 逐帧、语音识别逐段都是长任务，现在都会打印进度：

```text
  [OCR]  45.2%  已识别 12 条
  [语音识别]  78.5%  已转 34 段
```

- **交互终端**：原地刷新同一行，并按时间节流，不会刷屏
- **重定向到文件**（`> log.txt`）：改成每跨过 10% 打一行，避免日志被刷爆

> `openai-whisper` 的 `transcribe()` 是一次性返回的，拿不到逐段进度，
> 所以它会先打印音频时长让你知道要等多久。`faster-whisper` 可以逐段出，
> 所以有真实百分比。

---

## ⚠️ 已知限制

**重型说话人分离（pyannote 那类）仍未实现。**

`RoleLineExtractor` 的构造参数里有 `speaker_diarization`，但它**只是个空壳** ——
传 `True` 只会打印一条警告，不会有任何效果，依赖里也没有任何声纹/分离库。

> 本项目早期的模块 docstring 曾声称"支持说话人分离、多人声识别"，
> 与实现不符，**已于 2026-09-24 更正**。别按那个说法规划用途。

能用的替代方案是**轻量语气打标**（`--speaker-filter`，见上文）：
它判断的是「这句像不像角色本人」，而不是「这段音频里是谁在说」。
对「洗干净语料」这个目的足够，对「给每个角色各自建一份语料」还不够。

---

## 常见问题

**Q：报错找不到 PaddleOCR / paddle？**
`paddlepaddle-gpu` 和 `paddleocr` 的版本是互相约束的（见上表），
不要单独升级其中一个，否则容易出 PIR 模式相关的报错。

**Q：跑完了，但一句台词都没有，而且没有任何报错？**
这正是 2026-09-28 修掉的那个坑。**根因**：环境里装的是 PaddleOCR 3.x，
而旧代码按 2.x 的结构解析返回结果 → 抛异常 → 被 `try/except` 吞掉 →
**静默返回空**。现在已加兼容层，而且这类失败会**逐条打印前 3 次 + 结束时汇总**，
所以再遇到时**先翻日志里有没有 `OCR 调用失败`**。

排查顺序：
1. 看日志开头的 `PaddleOCR 版本：` 那一行，确认实际装的是哪个大版本
2. 看结尾有没有 `⚠️ OCR 调用失败 N/M 帧`
3. 确认 `--ocr_region` 真的框住了字幕带（**最常见的原因**）
4. 确认视频里有**硬字幕**（烧进画面里的）；外挂字幕文件不归 OCR 管

**Q：OCR 识别到一堆非台词内容（台标、字幕组信息、歌词）？**
代码里有 `_filter_text()` 做基础过滤，但规则有限。
建议先把 `--ocr_region` 收窄到只有台词的那一条带，比调过滤规则有效得多。

**Q：太慢了。**
按性价比排序：
1. `--disable_voice`（有硬字幕时直接砍掉整条 ASR 路径，最有效）
2. 确认跳帧开着（默认开）——字幕静止的时间本来就不该重跑 OCR
3. 调大 `--ocr_interval`（默认 5，可以试 10）
4. `--use_faster_whisper`
5. 换小一点的 `--whisper_model`（但别回到 base，宁可 medium）

**Q：输出里有繁体字？**
已用 `opencc` 统一转简体。如果仍有残留，检查 `_filter_text()` 的处理链路。

**Q：以前能跑出 100 句，现在只有 80 句了？**
看终端里那份「文本质量过滤」报告 —— 大概率是长度红线丢掉的（过短/过长）。
调 `--min-len` / `--max-len`；完全要原始结果就 `--no-clean`。

**Q：条目数比以前多了？**
这是刻意的：合并策略改成「相同文本才合并」之后，
以前被拼成一条连行的相邻台词，现在会各自成条。带 `--format jsonl` 跑，
能直接看出每条是哪个通道、什么时间来的。

**Q：`--speaker-filter` 报连不上 Ollama？**
打标会**自动跳过、数据不丢**。想用就先起 Ollama 并拉个模型：
`ollama pull qwen3:8b`。打分失败的句子会保留，只是没有 `speaker_score`。

---

## 测试

`tests/` 下三个脚本**全部离线**（不联网、不需要 GPU），直接跑：

```bash
cd zjy5
./.venv/Scripts/python.exe tests/run_tests.py
```

| 脚本 | 覆盖 |
|------|------|
| `test_text_clean.py` | 规范化、长度红线、长句切分、去重记频次、jsonl 读写、打标解析 |
| `test_merge_and_srt.py` | 合并策略（不拼连行 / 同句合并不重复）、SRT 不变量、质量过滤、三格式输出 |
| `test_harvest.py` | 批量收割：URL/清单解析、探测、断点续跑、jsonl 落盘 |

另外 `tests/smoke_ocr.py` 是**集成冒烟测试**（需要 paddleocr，会真跑一次 OCR）：
它临时合成一段带字幕的小视频，验证「识别准确 + 跳帧生效 + 相同文本会合并」。
