# 视频台词识别工具（chibtaici）

从视频里把**台词**抽出来，输出**去重纯文本**（喂给语料流程）或 **SRT 字幕**（保留时间轴）。

它是 `zjy5` 下的第二个工具，和 [`bilibili_downloader`](../bilibili_downloader/)
是**两件独立的事** —— 那个负责下载，这个负责识别。

---

## 两条识别路线

工具同时走两条路，**可以单独启用，也可以一起用**：

| 路线 | 用什么 | 适合 |
|------|--------|------|
| **OCR** | PaddleOCR，读画面下方字幕区域 | 视频**自带硬字幕** |
| **语音识别** | whisper / faster-whisper | 视频**没有字幕**，或字幕不完整 |

两条路的结果会**合并去重**（按时间邻近 + 文本相似），最后输出一份台词文本。

---

## 安装

依赖比较重（GPU 版 PaddleOCR + PyTorch），首次安装耗时较长：

```bash
cd zjy5/chibtaici
pip install -r requirements.txt
```

`requirements.txt` 里几个**刻意降级**的版本不是随手写的，改动前请先看注释：

| 包 | 版本 | 为什么钉住 |
|----|------|-----------|
| `opencv-python` | 4.6.0.66 | 兼容 PaddleOCR 2.7.0 |
| `paddlepaddle-gpu` | 2.6.2 | 解决 PIR 模式兼容性问题 |
| `paddleocr` | 2.7.0 | 兼容 PaddlePaddle 2.6.2 |
| `torch` / `torchvision` | 2.1.0+cu118 | 走 `--extra-index-url` 装 CUDA 11.8 版 |

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

> 更规范的做法是 `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 …` 再复制，
> 但要多下 1 GB 多；从已有的 torch 里复制零下载，效果一样。

### 怎么确认真的在用 GPU

```bash
python -c "from paddlex.utils.device import get_default_device; print(get_default_device())"
# 输出 gpu:0 才是真的在用 GPU
```

> **别只看 `paddle.device.is_compiled_with_cuda()`** —— 它返回 `True` 只说明
> 「编译时带了 CUDA」，不代表运行时能找到 CUDA 库、更不代表能真跑。
> 必须**实际跑一次运算**（或看 `get_default_device()`）。
>
> PaddleOCR 3.x 会自己调 `get_default_device()` 选设备，所以只要上面输出
> `gpu:0`，OCR 就自动走 GPU，代码不用改。

---

## 用法

```bash
python main.py --video 你的视频.mp4
```

输出默认写到 `role_lines.txt`。

### 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--video` | **必填** | 视频文件路径 |
| `--output` | `role_lines.txt` | 输出文件（`.srt` 由它推导出同名文件） |
| `--format` | `txt` | `txt`=去重纯文本（喂给微调流水线）、`srt`=带时间轴字幕、`both`=两者都出 |
| `--whisper_model` | `base` | `tiny`/`base`/`small`/`medium`/`large`，越大越准也越慢 |
| `--device` | `auto` | `auto`/`cpu`/`cuda`，`auto` 会优先用 CUDA |
| `--ocr_region` | `0,0.7,1,0.95` | OCR 区域，见下 |
| `--ocr_interval` | `5` | 每多少帧做一次 OCR（越大越快，但可能漏字） |
| `--use_faster_whisper` | 关 | **强制**用 faster-whisper；不传则自动挑已安装的后端（见下） |
| `--disable_ocr` | 关 | 只用语音识别 |
| `--disable_voice` | 关 | 只用 OCR |
| `--interactive_ocr` | 关 | 交互式设置 OCR 区域（命令行输入） |

### 只走一条路的例子

```bash
# 视频有硬字幕，不需要语音识别（快很多）
python main.py --video 番剧.mp4 --disable_voice

# 视频没字幕，只做语音识别
python main.py --video 录屏.mp4 --disable_ocr --whisper_model small

# 想更准一点
python main.py --video 番剧.mp4 --whisper_model medium --ocr_interval 3
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

## 输出

### `--format txt`（默认）：纯文本，一行一句

```text
你今天怎么来了？
真的吗？
我才不会答应你。
```

**不含时间戳，且会去重**——适合直接喂给后续的语料处理流程（比如 `zjy7` 的 LoRA 训练数据）。

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
> OCR 命中的是"某一帧"，本身只有时间点没有持续时间；转 SRT 时会补一个
> 最小持续时间（1.2 秒），并把结束时间夹在下一条开始之前，避免字幕重叠。

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

**说话人分离（多人声识别）尚未实现。**

`RoleLineExtractor` 的构造参数里有 `speaker_diarization`，但它**只是个空壳** ——
传 `True` 只会打印一条警告，不会有任何效果，依赖里也没有任何声纹/分离库。

> 本项目早期的模块 docstring 曾声称"支持说话人分离、多人声识别"，
> 与实现不符，**已于 2026-09-24 更正**。别按那个说法规划用途。

也就是说：**当前输出不区分说话人**，所有台词混在一起。
如果视频里有多人对话，需要自己按上下文判断。

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
2. 调大 `--ocr_interval`（默认 5，可以试 10）
3. `--use_faster_whisper`
4. 换小一点的 `--whisper_model`

**Q：输出里有繁体字？**
已用 `opencc` 统一转简体。如果仍有残留，检查 `_filter_text()` 的处理链路。
