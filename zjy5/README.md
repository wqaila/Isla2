# zjy5 —— B站下载器 + 视频台词识别

这个目录下是**两件独立的事**，不是一套流程的两个模块：

| 子目录 | 做什么 | 关系 |
|--------|--------|------|
| [`bilibili_downloader/`](bilibili_downloader/) | 从 B 站**下载**视频 / 音频 / 字幕 | 产出素材 |
| [`chibtaici/`](chibtaici/) | 从视频里**识别台词**，输出纯文本 | 消费素材 |

两者**没有代码依赖**，可以单独使用。放在一起只是因为它们常配合使用：
下载番剧 → 抽出台词 → 喂给 `zjy7` 做 LoRA 训练语料。

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

---

## 目录说明

| 目录 | 说明 |
|------|------|
| `downloads/` | 下载缓存的视频/音频，**不入库** |
| `.venv/` | 虚拟环境，**不入库** |
