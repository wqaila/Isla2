# Bilibili 视频/音频下载器

一个功能强大的 Bilibili 视频下载工具，支持视频/音频下载、频道视频爬取、搜索视频爬取和 AI 字幕提取。

## 功能特性

- 📥 **视频下载**: 支持下载 Bilibili 视频（最高质量）
- 🎵 **音频提取**: 支持只下载音频（MP3 格式）
- 📺 **频道爬取**: 获取频道最新/最热视频列表并下载
- 🔍 **搜索下载**: 搜索关键词并下载相关视频
- 📝 **AI 字幕**: 提取 Bilibili AI 生成的字幕
- 🔄 **批量下载**: 支持批量下载多个视频
- 🍪 **Cookie 管理**: 支持自动和手动获取 Cookie

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
| `-a`, `--audio-only` | `-a` | 仅下载音频 |
| `-s`, `--subtitle` | `-s` | 下载字幕（同时下载视频） |
| `--subtitle-only` | | 仅下载字幕（不下载视频） |
| `-p`, `--playlist` | `-p` | 下载播放列表 |
| `--cookie` | | Bilibili Cookie |
| `--version` | `-v` | 显示版本信息 |

### 排序方式

- `pubdate` / `latest`: 最新发布
- `hot`: 最热
- `view`: 最多播放
- `totalrank`: 综合排序
- `click`: 最多点击
- `stow`: 最多收藏

### Python API 使用

```python
from bilibili_downloader import BilibiliDownloader, ChannelDownloader, SearchDownloader, SubtitleExtractor

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
```

## Cookie 设置

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
1. Cookie 是否有效
2. 网络连接是否正常
3. yt-dlp 是否已安装
4. 视频 URL 是否正确

### Q: 字幕提取失败？

A: AI 字幕需要登录状态，请确保：
1. Cookie 包含 SESSDATA
2. 账号已登录
3. 视频确实有 AI 字幕

### Q: 如何更新 yt-dlp？

A: 运行以下命令：
```bash
pip install -U yt-dlp
```

## 许可证

MIT License

## 免责声明

本工具仅供学习和研究使用，请勿用于商业用途。下载的内容请支持正版。
