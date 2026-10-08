# zjy5 —— B站下载器 + 视频台词识别

这个目录下是**两件独立的事**，不是一套流程的两个模块：

| 子目录 | 做什么 | 关系 |
|--------|--------|------|
| [`bilibili_downloader/`](bilibili_downloader/) | 从 B 站**下载**视频 / 音频 / 字幕（含 SRT），以及**批量字幕收割** | 产出素材 |
| [`chibtaici/`](chibtaici/) | 从视频里**识别台词**，输出去重纯文本、SRT 字幕或 **jsonl** | 消费素材 |
| [`tests/`](tests/) | 两个工具的离线测试（不需要网络 / GPU） | 验证 |

两者**没有代码依赖**，可以单独使用。放在一起只是因为它们常配合使用：
下载番剧 → 抽出台词 → 喂给 `zjy7` 做 LoRA 训练语料。

---

## 采集语料的最短路径

**先问有没有字幕，再决定要不要跑识别。** 顺序错了会白烧几十小时 GPU：

```bash
# ① 先探测：这个 UP 有多少视频带 CC / AI 字幕？（不下载视频、不做 OCR）
cd bilibili_downloader
python -m bilibili_downloader --harvest "https://space.bilibili.com/123456" --probe-only

# ② 命中率还行就直接收割成 jsonl（带 BV 号 / 时间点 / 通道 / 置信度）
python -m bilibili_downloader --harvest "https://space.bilibili.com/123456" --max-count 100

# ③ 只有「没字幕」的那几个视频，才轮到 OCR / 语音识别兜底
cd ../chibtaici
python main.py --video ../downloads/xxx.mp4 --format jsonl --skip-head 90 --skip-tail 90
```

---

## 典型串联用法

```bash
# ① 下载（含 AI 字幕）
#    注意：视频链接是**位置参数**，不要写成 --url
cd bilibili_downloader
python -m bilibili_downloader "视频链接" --subtitle

# ② 识别台词（视频没有字幕时）
cd ../chibtaici
python main.py --video ../downloads/xxx.mp4 --disable_voice
```

---

## 跑测试

```bash
cd zjy5
./.venv/Scripts/python.exe tests/run_tests.py     # 三个离线脚本，不联网、不需要 GPU
```

---

## ⚠️ 凭据安全

`bilibili_downloader/bilibili_cookies.txt` 里是**真实的 B 站登录凭据**
（含 SESSDATA）。这个文件**永远不要提交**：

- 仓库的 `.gitignore` 已排除它
- 如果你曾经提交过，请到 B 站「账号安全」注销所有登录并改密码，
  再清理 git 历史（`git filter-repo`）

---

## 各自文档

- 下载器：[`bilibili_downloader/README.md`](bilibili_downloader/README.md)
- 台词识别：[`chibtaici/README.md`](chibtaici/README.md)
- **下游要什么**：[`台词之外还需要什么.md`](台词之外还需要什么.md)
  —— 训一个角色模型除台词还缺什么（人设 / 提问 / 知识库 / 负样本 / 通用数据 / 多轮 / 长度），
  带实测量化与建议顺序，以及**采集端能贡献哪几项**

---

## 2026-09-28 改动记录

按「采集工具改进方向」的优先级做了前四项 + 第五项的轻量版：

| 项 | 落地 |
|----|------|
| **批量字幕收割** | 新增 `bilibili_downloader/harvest.py`：UP 空间 / urls.txt → 翻页枚举 → 逐个探测字幕 → 命中直接抄文本（不下载、不 OCR）。`--probe-only` 看命中率、`harvest_state.json` 断点续跑 |
| **分页 + `--max-count`** | `ChannelCrawler.get_all_channel_videos()`；播放列表「默认只下 10 个」也接上了这个参数 |
| **jsonl 结构化** | `--format jsonl/all`：`text/video/start/end/channel/confidence`，收割端与识别端**字段对齐** |
| **whisper 升级** | 默认 `base` → `large-v3`；faster-whisper 开 VAD；`--hotwords` 注入专有名词 |
| **OCR 跳帧** | 区域缩略图指纹比对，实测跳过 **88%** 的 OCR 调用；顺带让 OCR 条目有了真实持续时间 |
| **合并策略** | 改为「相同 / 相似 / 包含才算同一条」，不再拼连行、不再拼重复 |
| **质量红线** | 新增 `chibtaici/text_clean.py`：繁→简、全半角、去表情、4~100 字、超长按标点切、掐头去尾、去重记频次 |
| **语气打标** | 新增 `chibtaici/speaker_filter.py`：本地 Ollama 打 1-5 分，低分剔除；Ollama 不可用时**不丢数据** |
| **测试** | 新增 `tests/`：三个离线脚本（17 个测试函数）+ 一个 OCR 集成冒烟 |

**两处刻意保留默认值的决定**：

- `--require-complete` 默认关 —— ASR 输出常整段没标点，默认按「标点收尾」过滤会一次损失大量语料
- `--skip-head/--skip-tail` 默认 0 —— 默认掐头去尾会静默丢内容、落盘找不回；
  推荐值（`90`）写在文档里，由你显式加

**行为变化**：合并策略改掉之后，txt 的条目数会比以前多（以前被拼成连行的相邻台词现在各自成条）。
这是刻意的，想要可回溯产物就加 `--format all`。

> 完整背景见 [`台词之外还需要什么.md`](台词之外还需要什么.md) 的附录。

---

## 目录说明

| 目录 | 说明 |
|------|------|
| `downloads/` | 下载缓存的视频/音频，**不入库** |
| `.venv/` | 虚拟环境，**不入库** |

## 语料后处理工具（2026-10-08 新增）

源视频往往采集完就删了，所以「重跑一遍流水线」通常不现实。
下面两个脚本专门给**已经落盘的 txt** 做补救：

| 脚本 | 作用 |
|------|------|
| `tag_speakers.py` | 给现有语料打**角色相似度**分（哪句像角色本人说的），输出 `speaker_tags/*.jsonl`；可另存过滤后的 txt |
| `chibtaici/term_fix.py` | **OCR 术语校正** —— 把识别错的角色名改回来（芽依→芽衣、独心术→读心术…）；同时能审计疑似错写 |

```bash
cd zjy5
python tag_speakers.py --dry-run              # 先看要处理多少句
python tag_speakers.py --filtered-dir out     # 全量打标 + 输出过滤后语料
python -m chibtaici.term_fix                  # 审计 OCR 错写
```

详见 `chibtaici/README.md` 的「语气打标」与「OCR 术语校正」两节。
