# website/ —— 独立展示站

## 这个目录是什么

这里放的是**独立的静态展示页**，用来对外介绍项目全貌，**不会被后端服务提供**。

后端服务只挂载 `server/static/`（见 `server/config.py` 的 `STATIC_DIR`，以及 `main.py` 里的
`app.mount("/static", ...)` 和 `/dashboard` 路由）。所以：

| 文件 | 用途 | 谁来提供 |
|------|------|---------|
| `website/index.html` | 项目介绍页（对外展示用） | ❌ 后端不提供 |
| `website/projects.html` | 项目全景汇总（对外展示用） | ❌ 后端不提供 |
| `server/static/index.html` | **管理面板**（实时日志/会话/记忆库） | ✅ `/dashboard` 或 `/static/index.html` |
| `server/static/projects.html` | 项目全景汇总（另一版） | ✅ `/static/projects.html` |

## 怎么本地预览

```bash
cd website
python -m http.server 8000
# 然后打开 http://localhost:8000/index.html
```

## ✅ 重复维护点已处理（2026-09-21）

`website/projects.html` 和 `server/static/projects.html` 原本是同一份内容的**两代版本**：

| 版本 | 编号方式 | 状态 |
|------|---------|------|
| `server/static/projects.html` | `zjy2`~`zjy9`（与实际目录一致） | ✅ **已定为唯一版本** |
| `website/projects.html`（旧） | `c1`~`c6` | ❌ 已废弃 |

**决定：保留 zjy 编号版**（因为它和仓库实际的目录结构一一对应，不会让人对不上号）。

现在两份文件**内容完全一致**（同一个 SHA），任改一份都要同步另一份。
如果以后只想保留一份，删掉 `website/projects.html` 即可 ——
该页面在 `website/` 里也没有任何入口链接，属于独立文件。

> 旧的 c1~c6 版本仍可从 git 历史中找回（提交 `88504321` 之前的版本）。
