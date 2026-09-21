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

## ⚠️ 已知的重复维护点

`website/projects.html` 和 `server/static/projects.html` 是**同一份内容的两代版本**，
但标题、配色、CSS 写法都已经不一样了：

- `website/projects.html` —— 较新（标题「次元萌盒 · 项目全景汇总」，重写过样式）
- `server/static/projects.html` —— 较早（标题「赛博玩具 AI · 项目全景汇总」）

**改其中一份不会影响另一份。** 如果只想保留一个版本，建议：

1. 确定以哪一份为准；
2. 把它复制到另一处覆盖；
3. 或者删掉 `server/static/projects.html`（该页面目前没有任何入口链接，属于孤儿页面）。

在此之前，请记住「改一处要同步另一处」，或者干脆只维护 `website/` 这一份。
