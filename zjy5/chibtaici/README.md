# 视频台词识别工具（chibtaici）

从视频里把**台词**抽出来，输出**纯文本**（无时间戳，一行一句）。

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

> 没有 NVIDIA 显卡也能跑：把 `paddlepaddle-gpu` 换成 `paddlepaddle`（CPU 版），
> 并在命令行加 `--device cpu`。只是会慢很多。

另外 `opencc-python-reimplemented` 用来把识别结果**统一转成简体**。

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
| `--output` | `role_lines.txt` | 输出文件 |
| `--whisper_model` | `base` | `tiny`/`base`/`small`/`medium`/`large`，越大越准也越慢 |
| `--device` | `auto` | `auto`/`cpu`/`cuda`，`auto` 会优先用 CUDA |
| `--ocr_region` | `0,0.7,1,0.95` | OCR 区域，见下 |
| `--ocr_interval` | `5` | 每多少帧做一次 OCR（越大越快，但可能漏字） |
| `--use_faster_whisper` | 关 | 用 faster-whisper 替代 openai-whisper（更快更轻） |
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

纯文本，一行一句，**不带时间戳**：

```text
你今天怎么来了？
真的吗？
我才不会答应你。
```

适合直接喂给后续的语料处理流程（比如 `zjy7` 的 LoRA 训练数据）。

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
