# Phase 6：Web 管理界面

> 日期：2026-11-16
> 技术栈：React 18 + TypeScript 5 + Vite 5
> 结果：**完成**。8 个页面，构建通过，30 项端到端验收通过

---

## 一、交付内容

### 1.1 8 个页面

| 页面 | 路由 | 核心功能 |
|---|---|---|
| 仪表盘 | `/dashboard` | GPU 显存、引擎诊断、服务状态、能力说明 |
| 角色管理 | `/characters` | CRUD、绑定声音、情绪标签（元数据） |
| 声音管理 | `/speakers` | 导入、质量检测报告、重检、删除保护 |
| 情绪管理 | `/emotion` | 标签定义 + **对照试听验证无效** + 替代方案 |
| 合成测试 | `/synthesis` | 语速/音量/停顿调节、自然语言指令、历史对比 |
| 设备管理 | `/devices` | ESP32 协议约定（Phase 5 才联调，不伪造数据） |
| 系统设置 | `/system` | 配置只读展示、路径、配置来源、数据库状态 |
| 日志中心 | `/logs` | 日志格式说明 + 常用排查命令（可复制） |

### 1.2 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 框架 | React 18 | 生态成熟，团队熟悉 |
| 语言 | TypeScript 5 | 严格模式，与后端 Pydantic 模型对齐 |
| 构建 | Vite 5 | 冷启动快，dev proxy 方便 |
| 路由 | react-router-dom 6 | 标准方案 |
| 样式 | 原生 CSS | 无额外依赖，体积小（4.55KB） |
| 状态 | 自研 `useAsync` hook | 只有 loading/error/reload，不需要 Redux |

**刻意不引入**：UI 组件库、状态管理库、CSS 框架。
理由：8 个页面的复杂度不值得引入额外依赖，且能减少构建体积（总 JS 203KB，gzip 66KB）。

### 1.3 关键设计

**1. API 层统一错误处理**

后端返回 `{code, message, detail}`，前端转成 `ApiError`：

```typescript
class ApiError extends Error {
  code: string;
  status: number;
  detail?: Record<string, unknown>;
}
```

`describeError()` 会把 `detail.blocking_issues` 等结构化信息拼进可读文本，
让"参考音频质量不达标"这类错误直接显示原因，而不是笼统的 "400 Bad Request"。

**2. 合成接口单独处理**

`/synthesize` 返回二进制 WAV + 6 个响应头，不能用普通 JSON 封装：

```typescript
async synthesize(body): Promise<{ blob: Blob; meta: SynthesisMeta }> {
  const res = await fetch("/synthesize", {...});
  const blob = await res.blob();
  const meta = { duration: h.get("x-duration"), rtf: h.get("x-rtf"), ... };
  return { blob, meta };
}
```

**3. object URL 生命周期管理**

合成结果用 `URL.createObjectURL` 生成播放地址，组件卸载时必须 revoke，
否则内存泄漏：

```typescript
const urls = useRef<string[]>([]);
useEffect(() => () => urls.current.forEach(URL.revokeObjectURL), []);
```

**4. 情绪页如实告知**

这是本阶段最重要的设计决策。情绪页**不是**让人以为情绪能控制，
而是主动展示证据：

- 顶部黄色警告框直接说明"本模型不支持情绪控制"
- 提供「生成对照样本」按钮，一键合成 6 种情绪的同一句话
- 用户亲耳听到 6 个样本几乎一样，比任何文档都有说服力
- 附「替代方案」表格：想开心就让 LLM 输出「嘿嘿~」

**5. 不伪造数据**

设备页明确写"暂无在线设备，Phase 5 才联调"，而不是塞假数据撑门面。
系统页标注"本页为只读，修改请编辑 configs/gpu.yaml"。

---

## 二、目录结构

```text
web/
├─ index.html
├─ package.json
├─ tsconfig.json          # 严格模式 + noUncheckedIndexedAccess
├─ tsconfig.node.json
├─ vite.config.ts         # dev proxy 到 :50001
└─ src/
   ├─ main.tsx            # 入口 + BrowserRouter
   ├─ App.tsx             # 布局 + 路由
   ├─ styles.css          # 暗色主题，4.55KB
   ├─ api/
   │  ├─ types.ts         # 与后端 schemas 对齐的类型
   │  └─ client.ts        # fetch 封装 + ApiError + 4 组 API
   ├─ hooks/
   │  └─ useAsync.ts      # loading/error/reload
   ├─ components/
   │  └─ ui.tsx           # Card/Metric/Badge/Alert/Loading/Empty
   └─ pages/
      ├─ DashboardPage.tsx
      ├─ CharactersPage.tsx
      ├─ SpeakersPage.tsx
      ├─ EmotionPage.tsx
      ├─ SynthesisPage.tsx
      ├─ DevicesPage.tsx
      ├─ SystemPage.tsx
      └─ LogsPage.tsx
```

---

## 三、构建与运行

### 3.1 开发模式

```bash
# 终端 1：后端
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/run_server.sh

# 终端 2：前端
cd D:\xiangmudaima\zjy14\web
npm run dev
# 打开 http://localhost:5173
```

Vite dev server 把 `/api`、`/health`、`/synthesize` 代理到 `127.0.0.1:50001`，
避免 CORS。

### 3.2 生产构建

```bash
cd web
npm run build      # 输出到 web/dist/
```

构建产物：

```
dist/index.html                   0.42 kB │ gzip:  0.28 kB
dist/assets/index-*.css           4.55 kB │ gzip:  1.54 kB
dist/assets/index-*.js          203.87 kB │ gzip: 66.60 kB
```

### 3.3 部署方式

前端是纯静态文件，两种部署：

**方案 A（推荐）**：Nginx / Caddy 托管 `dist/`，反代 `/api` 到后端
**方案 B**：把 `dist/` 挂到 FastAPI 的 StaticFiles（需改后端代码）

当前后端未内置静态托管，因为按决策 D10 默认仅监听 localhost，
生产部署应该由用户显式配置反向代理。

---

## 四、验收结果

### 4.1 端到端（30 项全通过）

```
[1] 前端构建产物      3 项 PASS（index.html / JS bundle / CSS bundle）
[2] 源码文件完整性    7 项 PASS
[3] 8 个页面文件      8 项 PASS
[4] 后端启动          PASS（stub 引擎 3s 就绪）
[5] 前端依赖的接口    5 项 PASS
[6] 合成接口          3 项 PASS（含 x-duration / x-rtf 响应头）
[7] 自然语言指令      PASS
[8] 情绪标签          PASS
[9] CORS 检查         已说明（dev 用 proxy 规避）
[10] node_modules     PASS
```

### 4.2 真实联调验证

启动 dev server + stub 后端，通过 Vite proxy 访问：

```
/health                  -> 200 (336 bytes)
/api/v1/system/status    -> 200 (1252 bytes)
/api/v1/characters       -> 200 (1281 bytes)
/api/v1/speakers         -> 200 (957 bytes)

/dashboard  -> 200（SPA 回退正常）
/synthesis  -> 200
/emotion    -> 200
```

### 4.3 类型检查

```
tsc --noEmit  ->  0 errors
```

严格模式开启，含 `noUncheckedIndexedAccess`、`noUnusedLocals`、`noUnusedParameters`。

---

## 五、开发中遇到的问题

### 5.1 npm 被 PowerShell 执行策略拦住

```
npm : 无法加载文件 D:\New Folder\npm.ps1，因为在此系统上禁止运行脚本
```

解决：直接用 `npm.cmd` 而非 `npm`，绕过 ps1 脚本。

### 5.2 esbuild postinstall 被 npm 11 新策略拦截

```
npm warn install-scripts  esbuild@0.21.5 (postinstall: node install.js)
```

检查发现 `node_modules/@esbuild/win32-x64/esbuild.exe` 已存在（0.21.5），
二进制预编译包正常，不需要批准 postinstall。

### 5.3 TypeScript 项目引用配置错误

```
error TS6310: Referenced project 'tsconfig.node.json' may not disable emit
```

`composite: true` 的项目不能设 `noEmit: true`。
改为 `emitDeclarationOnly: true` + 指定 `outDir`。

### 5.4 `vite.config.ts` 用了 `process.env` 但缺 Node 类型

```
error TS2580: Cannot find name 'process'
```

两个选择：装 `@types/node`，或用 Vite 的 `loadEnv`。
选后者——避免为一个配置项引入整个 Node 类型包。

### 5.5 类型收紧抓到一个真 bug

最初 `SynthesisPage` 用 `Record<string, unknown>` 传参给 `synthesize()`，
类型检查报：

```
Argument of type 'Record<string, unknown>' is not assignable to parameter of type 'SynthesisRequest'
```

这说明如果当时用了 `any`，字段名写错也不会被发现。改成显式构造
`SynthesisRequest` 后，字段名和类型都有保障。

---

## 六、仍未完成

| 项 | 说明 |
|---|---|
| 设备实时状态 | Phase 5 联调后才有数据 |
| 日志在线查看 | 后端未提供 `/api/v1/logs` 接口，当前只给排查命令 |
| 合成记录查询 | 数据在 SQLite 里，未做 API |
| 鉴权 | D10 默认仅 localhost，未做登录 |
| 国际化 | 仅中文 |
| 移动端适配 | 未做响应式，桌面优先 |

---

## 七、复现命令

```bash
# 类型检查
cd web && npm.cmd run typecheck

# 构建
cd web && npm.cmd run build

# 端到端验收（会自动启动 stub 后端）
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/verify_phase6.sh

# 开发模式
cd web && npm.cmd run dev
```

---

## 八、截图说明

无法自动截图（无浏览器自动化环境），但已验证：

- dev server 返回正确的 HTML（含 React Refresh 注入）
- 所有 API 通过 proxy 返回 200
- SPA 路由回退正常
- 构建产物无错误

**建议你手动打开 `http://localhost:5173` 看一眼实际渲染效果**，
特别关注：

1. 仪表盘的 GPU 显存数字是否与 `nvidia-smi` 一致
2. 合成测试页的音频能否播放
3. 情绪页的对照样本是否听得出差异（预期听不出）
