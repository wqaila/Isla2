# Bilibili 视频/音频下载器

一个功能强大的 Bilibili 视频下载工具，支持视频/音频下载、频道视频爬取、搜索视频爬取和 AI 字幕提取。

## 功能特性

- 📥 **视频下载**: 支持下载 Bilibili 视频（最高质量）
- 🎵 **音频提取**: 支持只下载音频（MP3 格式）
- 📺 **频道爬取**: 获取频道最新/最热视频列表并下载
- 🔍 **搜索下载**: 搜索关键词并下载相关视频
- 📝 **AI 字幕**: 提取 Bilibili AI 生成的字幕
- 🧺 **批量字幕收割**: 给一个 UP 空间或 urls.txt，自动逐个探测并抄下 CC/AI 字幕（**不下载视频、不做 OCR**）
- 🔄 **批量下载**: 支持批量下载多个视频
- 🍪 **Cookie 管理**: 支持自动和手动获取 Cookie
- 📊 **实时进度**: 下载时原地刷新显示百分比、速度与剩余时间

## 安装

### 1. 克隆项目

```bash
git clone <repository-url>
cd bilibili_downloader
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 安装 Playwright 浏览器（首次使用）

```bash
playwright install
```

## 使用方法

### 命令行使用

#### 下载单个视频

```bash
# 下载单个视频
python -m bilibili_downloader "https://www.bilibili.com/video/BV1xx411c7mD"

# 下载单个音频
python -m bilibili_downloader "https://www.bilibili.com/video/BV1xx411c7mD" --audio

# 下载视频并提取 AI 字幕
python -m bilibili_downloader "https://www.bilibili.com/video/BV1xx411c7mD" --sub
```

#### 下载频道视频

```bash
# 下载频道最新 10 个视频
python -m bilibili_downloader --channel "https://space.bilibili.com/123456" --max 10

# 下载频道最热视频
python -m bilibili_downloader --channel "https://space.bilibili.com/123456" --order hot
```

#### 下载搜索结果

```bash
# 下载搜索结果
python -m bilibili_downloader --search "Python 教程" --max 5
```

#### 只下载字幕

```bash
# 只下载 AI 字幕（纯文本格式）
python -m bilibili_downloader "https://www.bilibili.com/video/BV1xx411c7mD" --subtitle-only

# 下载 SRT 格式字幕
python -m bilibili_downloader "https://www.bilibili.com/video/BV1xx411c7mD" --subtitle-only --format srt
```

#### 批量字幕收割（采集语料最快的一条路）

`--subtitle-only` 一次只能喂一个 URL，手动贴几百个链接不现实 ——
`--harvest` 把它接上批处理：给一个 UP 主空间 URL 或一份 `urls.txt`，
自动枚举视频、逐个探测字幕，命中的直接把文本抄下来。
**不下载视频、不做 OCR、不做语音识别。**

```bash
# 先探一把：这个 UP 有多少视频带字幕？（不落盘，只给命中率）
python -m bilibili_downloader --harvest "https://space.bilibili.com/123456" --probe-only

# 真的收割：默认汇总到一个 jsonl（句级溯源）
python -m bilibili_downloader --harvest "https://space.bilibili.com/123456" --max-count 50

# 从清单文件收割（一行一个 URL 或裸 BV 号，# 开头是注释）
python -m bilibili_downloader --harvest urls.txt

# 想按视频各存一个 txt
python -m bilibili_downloader --harvest "https://space.bilibili.com/123456" --harvest-format txt
```

**断点续跑**：状态写在 `<输出目录>/harvest_state.json`（按 BV 号记录结果）。
中途 Ctrl-C 之后重跑会自动跳过已完成的；「无字幕」也会记下来，
不会每次都白探一遍。失败项要重试就加 `--retry-failed`。

> **探测不吃掉断点**：`--probe-only` 命中的视频记的是「probed（有字幕，正文未取）」，
> 不是「已完成」—— 接着跑正式收割时，它们照样会被重新取一次正文。
> （否则「先探测再收割」这条推荐用法会直接失效：探测完一看，全被跳过了。）

**先探测再决定**：字幕那一路又快又准，所以**先问有没有**。
命中率低于三成的话，剩下没字幕的视频再走 `chibtaici` 的 OCR / 语音识别兜底。

#### 交互式模式

```bash
python -m bilibili_downloader --interactive
```

### 命令行参数

| 参数 | 简写 | 说明 |
|------|------|------|
| `url` | | 视频 URL（位置参数） |
| `-o`, `--output` | `-o` | 输出目录（默认：downloads） |
| `-q`, `--quality` | `-q` | 视频质量（best/1080/720/480） |
| `-a`, `--audio`, `--audio-only` | `-a` | 仅下载音频 |
| `-s`, `--sub`, `--subtitle` | `-s` | 下载字幕（同时下载视频） |
| `--subtitle-only` | | 仅下载字幕（不下载视频） |
| `--format` | | 字幕输出格式（text/srt，默认：text） |
| `-p`, `--playlist` | `-p` | 下载播放列表 |
| `--channel` | | 频道/UP 主空间 URL |
| `--search` | | 搜索关键词 |
| `--max` | | 频道/搜索最多下载数量（默认：10） |
| `--max-count` | | 收割/播放列表最多处理多少个（0=不限；不给则沿用 `--max`） |
| `--harvest` | | 批量字幕收割：UP 空间 URL、视频 URL 或 urls.txt 清单 |
| `--urls-file` | | 配合 `--harvest`：一行一个 URL 的清单文件 |
| `--state` | | 收割断点状态文件（默认 `<输出目录>/harvest_state.json`） |
| `--harvest-format` | | `jsonl`（默认，汇总成一个文件）或 `txt`（一个视频一个文件） |
| `--rate-limit` | | **每次请求**之间的间隔秒数（默认 1.0；0 表示不限速） |
| `--probe-only` | | 只探测有没有字幕，不取正文、不落盘 |
| `--retry-failed` | | 重试上次失败的条目（「无字幕」默认不重试） |
| `--order` | | 排序方式（pubdate/hot/view/totalrank/click/stow） |
| `--interactive` | | 进入交互式模式 |
| `--cookie` | | Bilibili Cookie |
| `--check-cookie` | | **联网检查 Cookie 是否还有效**，然后退出（不下载任何东西） |
| `--no-check-certificate` | | 跳过 HTTPS 证书校验（默认校验） |
| `-v`, `--version` | `-v` | 显示版本信息 |

**下载行为微调**（这些原先**全部硬编码**在代码里，改不了）：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--no-resume` | 关（即**续传默认开**） | 关闭断点续传。大文件下到一半中断后，重跑默认**从断点继续**而不是从头下 |
| `--sleep-interval` | `3` | 每次请求之间的最小间隔秒数（反爬） |
| `--max-sleep-interval` | `10` | 间隔的随机上限（yt-dlp 在两者之间随机取） |
| `--retries` | `3` | 单个视频的重试次数 |
| `--retry-sleep` | `5` | 重试前的等待秒数 |
| `--audio-quality` | `0` | 音频质量，0 最好、9 最差 |
| `--merge-format` | `mp4` | 音视频合并后的容器格式 |
| `--retry-downloads` | 关 | **只重试上次下载失败的**（读失败清单，成功自动移除） |
| `--failure-list` | `<输出目录>/failed_downloads.json` | 失败清单文件路径 |

> 💡 **网络差就调大 `--retries` / `--retry-sleep`；怕被风控就调大 `--sleep-interval`；
> 想要小体积音频就调大 `--audio-quality`。**
> 这些默认值与改动前**完全一致** —— 纯增量，不改变原有行为。

### 批量下载失败了怎么补？（`--retry-downloads`）

批量下载 100 个视频、第 80 个挂了 —— 以前只会在屏幕打一行 `✗ 下载失败`
然后继续，**失败原因与清单全部丢失**，重跑只能靠「文件在不在」猜。

现在每次失败都会记进 `failed_downloads.json`（含 URL、原因、次数、分类）：

```bash
# 只补上次没下成功的（成功过的会自动从清单移除）
python -m bilibili_downloader --retry-downloads

# 指定清单位置
python -m bilibili_downloader --retry-downloads --failure-list my.json
```

失败原因会**自动分类**，因为分类决定了「该不该重试」：

| 分类 | 含义 |
|------|------|
| `network` | 网络问题 —— **重试有意义** |
| `risk_control` | 风控拦截 —— 等一会儿再试，别硬撞 |
| `http_403` | Cookie 失效或缺权限 —— 重试无用，先 `--check-cookie` |
| `not_found` | 视频被删或 URL 错 —— 重试无用 |
| `ssl` | 证书问题 —— 加 `--no-check-certificate` |

> ⚠️ 注意区分：`--retry-downloads` 是**下载**失败重试；
> `--retry-failed` 是**字幕收割**失败重试（harvest 用）。两者独立。
>
> ⚠️ 清单写入是**原子**的（先写 `.tmp` 再替换），中途被打断不会留下半个坏文件；
> 清单损坏时按空清单继续，不会中断下载。

> 注：`--channel`、`--search` 等接口需要 WBI 签名（已内置实现）；
> 若签名或请求失败会打印明确错误，不会静默返回空列表。

### 排序方式

- `pubdate` / `latest`: 最新发布
- `hot`: 最热
- `view`: 最多播放
- `totalrank`: 综合排序
- `click`: 最多点击
- `stow`: 最多收藏

### Python API 使用

```python
from bilibili_downloader import (BilibiliDownloader, ChannelDownloader,
                                 SearchDownloader, SubtitleExtractor,
                                 SubtitleHarvester)

# 下载单个视频
downloader = BilibiliDownloader(output_dir="downloads")
downloader.download_video("https://www.bilibili.com/video/BV1xx411c7mD")

# 下载音频
downloader.download_audio("https://www.bilibili.com/video/BV1xx411c7mD")

# 下载频道视频
channel_downloader = ChannelDownloader(output_dir="downloads")
channel_downloader.download_by_url("https://space.bilibili.com/123456", max_videos=10)

# 下载搜索结果
search_downloader = SearchDownloader(output_dir="downloads")
search_downloader.download_search_results("Python 教程", max_videos=5)

# 提取字幕
extractor = SubtitleExtractor()
extractor.extract_subtitle("https://www.bilibili.com/video/BV1xx411c7mD")

# 只探测有没有字幕（一个请求，不落盘）
info = SubtitleExtractor().probe_subtitle("https://www.bilibili.com/video/BV1xx411c7mD")
print(info["has_subtitle"], info["lang"])

# 批量收割
harvester = SubtitleHarvester(output_dir="downloads", state_path="downloads/harvest_state.json")
harvester.run(target="https://space.bilibili.com/123456", max_count=100)
```

### 收割产物：jsonl 长什么样

```json
{"text": "悲剧并非终结，而是希望的起始。", "video": "BV1xx411c7mD", "start": 123.4, "end": 126.8, "channel": "subtitle", "confidence": null, "lang": "zh-CN", "ai_type": 1}
```

`confidence` 对字幕通道**永远是 `null`** —— B 站字幕接口不提供置信度，
这里如实写 null，不去编一个看起来很像的数字。

> 这个 jsonl 与 `chibtaici --format jsonl` 的字段是对齐的，
> 下游可以混着吃（字幕通道 + OCR 通道 + 语音通道一起）。

## 下载进度

下载时会在原地刷新显示进度，不会把终端刷满：

```text
  正在下载：某视频
  [download] Destination: 某视频.f100026.mp4
  [download]  45.2% of  123.45MiB at  2.10MiB/s ETA 00:32
  [Merger] Merging formats into "某视频.mp4"
```

进度行（含百分比的 `[download]` 行）用回车覆盖同一行；其它行（Destination、
合并、报错等）正常换行输出。

> **为什么之前看不到百分比**：yt-dlp 默认用 `\r` 原地刷新进度条，被管道
> 接住时会攒成一大块、最后才一起冒出来。所以命令里加了 `--newline`
> 让进度按行输出，再统一整理。如果你要自己写脚本调 yt-dlp，也要加这个参数。

需要拿到原始输出行做自定义处理时，可以用回调：

```python
downloader = BilibiliDownloader()
downloader.progress_callback = lambda line: print("RAW:", line)
downloader.download_video(url)
```

---

## Cookie 设置

### 先检查 Cookie 还有没有效（推荐第一步）

```bash
python -m bilibili_downloader --check-cookie
```

会联网问一次服务端，输出用户名或失败原因，**失败时退出码非 0**（可用于脚本判断）。

> ⚠️ **为什么不能只看「文件里有没有 SESSDATA」**
> Cookie 过期后 B 站接口**不报错**，只会**静默返回未登录结果** ——
> 表现为「字幕全都探测不到」「视频信息取不到」，
> 看起来像风控或接口改版，**很难联想到是 Cookie 过期**。
> 所以有一个联网预检，把「凭据失效」和「网络不通」**分开报**：
> 网络故障时不会说成「Cookie 失效」（否则你会白去重新登录）。

### 自动获取 Cookie

首次使用时，程序会提示是否使用 Playwright 自动获取 Cookie：

```
是否使用 Playwright 自动获取 Cookie? (y/n): y
```

浏览器会打开 Bilibili 登录页面，登录后等待几秒钟即可自动保存 Cookie。

### 手动获取 Cookie

1. 打开浏览器（Chrome/Edge/Firefox）
2. 访问 https://www.bilibili.com 并登录账号
3. 按 F12 打开开发者工具
4. 切换到 Network（网络）标签
5. 刷新页面（F5）
6. 找到任意一个 Bilibili 的 API 请求
7. 在请求头（Headers）中找到 Cookie
8. 复制 Cookie 值并保存到 `bilibili_cookies.txt` 文件

Cookie 文件格式（Netscape 格式）：

```
# Netscape HTTP Cookie File
.example.com	TRUE	/	FALSE	1234567890	name	value
```

## 注意事项

1. **反爬机制**: 程序内置了请求间隔和重试机制，但仍请合理使用
2. **下载速度**: 下载速度取决于网络状况和 Bilibili 服务器
3. **字幕提取**: AI 字幕需要登录状态，请确保 Cookie 有效
4. **文件命名**: 视频文件使用标题命名，可能会包含特殊字符

## 常见问题

### Q: 下载失败怎么办？

A: 请检查：
1. Cookie 是否有效（可先跑 `--check-cookie` 确认）
2. 网络连接是否正常（网络差就调大 `--retries` 与 `--retry-sleep`）
3. yt-dlp 是否已安装
4. 视频 URL 是否正确

> 💡 **大文件下到一半中断了？** 直接重跑即可 —— **默认开启断点续传**
> （`--continue`），会从断点继续而不是从头下。
> 只有极少数「想重新下一份干净的」场景才需要 `--no-resume`。
>
> ⚠️ 这个默认值是 2026-10-12 才加上的。在此之前没有传 `--continue`，
> 一个 2GB 视频下到 1.5GB 挂掉就要**从头重下**。

### Q: 字幕提取失败？

A: AI 字幕需要登录状态，请确保：
1. Cookie 包含 SESSDATA
2. 账号已登录
3. 视频确实有 AI 字幕

### Q: 收割跑完了，命中率是 0？

A: 说明这个 UP 的视频里确实没有 CC / AI 字幕（AI 字幕要 UP 主开了「字幕」或
平台自动生成才有）。这时才轮到 `chibtaici` 的 OCR / 语音识别兜底。
先换几个 UP 用 `--probe-only` 探一下，比一上来就跑 OCR 划算得多。

### Q: 收割中断了怎么办？

A: 直接重跑同一条命令。状态在 `harvest_state.json` 里，已完成的会跳过，
只补没做的部分。想连失败项一起重试就加 `--retry-failed`。

### Q: 报「-352」/ 被风控拦了怎么办？

A: `-352` 是 B 站的**风控码**（签名校验失败或请求过于频繁），
与普通网络错误**分开处理**：

- 收割时会**连续 3 次风控后自动停止整批**，不再继续撞墙
  （风控通常是会话级 / IP 级的，继续跑只会加重它）
- 提示里会说明「剩余 N 个请求已停止」
- **普通错误（403 / 超时）不会触发中断**，只记录后继续 —— 偶发异常不该中断整批

处理办法：等一段时间后重跑（已完成的会自动跳过），或调大 `--rate-limit`、
更换 Cookie。

> ⚠️ **`--rate-limit` 对每一次请求生效**（2026-10-12 修正）。
> 旧实现只在「成功收割」后才 sleep，于是 `--probe-only` 探测上千个视频时
> **一个请求都不限速** —— 那是最容易触发风控的用法。
> 现在成功的、失败的、无字幕的请求都会限速（跳过的不算，它没发请求）。

### Q: 合集一次只下 10 个？

A: 老默认值确实偏小。用 `--max-count 50` 覆盖（`--max-count 0` 在收割里表示不限）。

### Q: 如何更新 yt-dlp？

A: 运行以下命令：
```bash
pip install -U yt-dlp
```

---

## 测试

```bash
cd zjy5
./.venv/Scripts/python.exe tests/test_harvest.py
```

`test_harvest.py` 用假的提取器/爬虫注入，**全程不联网**，
覆盖 URL 与清单解析、探测命中、断点续跑（含「跳过时不该再发请求」）、
失败项重试、probe-only 不落盘、jsonl 与 txt 两种产物。

## 许可证

MIT License

## 免责声明

本工具仅供学习和研究使用，请勿用于商业用途。下载的内容请支持正版。
