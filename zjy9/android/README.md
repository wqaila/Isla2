# 爱莉希雅 AI —— Android 客户端

Kotlin + Jetpack Compose 写的手机端，通过 **WebSocket** 连接 `zjy9/server`，
支持流式回复。

---

## 项目结构

```
android/
├── app/src/main/java/com/elysia/ai/
│   ├── MainActivity.kt              入口
│   ├── data/
│   │   ├── Models.kt                数据模型（含 ServerConfig）
│   │   └── PreferencesManager.kt    本地设置持久化
│   ├── network/
│   │   └── WebSocketClient.kt       WS 客户端（含令牌与重连）
│   ├── ui/
│   │   ├── screens/ChatScreen.kt    聊天界面
│   │   ├── screens/SettingsScreen.kt 设置界面
│   │   └── theme/                   主题
│   └── viewmodel/
│       └── ChatViewModel.kt         状态与业务逻辑
├── run-build.bat                    一键构建（见下）
└── local.properties                 Android SDK 位置（**不入库**）
```

---

## 构建

### 一键构建

```bat
run-build.bat
```

产物：`app\build\outputs\apk\debug\app-debug.apk`

> 这个脚本原本硬编码了**另一台机器的路径**（`C:\Users\431\...`、
> `D:\zjy9\android`），在本机根本跑不起来，已于 2026-09-24 重写为
> 使用项目自带的 `gradlew` 并自动定位目录。

### 手动构建

```bash
cd zjy9/android
export JAVA_HOME="C:/Users/user/.workbuddy-ai/binaries/jdk/jdk17.0.20_10"
export ANDROID_HOME="D:/Android/Sdk"
./gradlew assembleDebug
```

首次构建约 **13.5 分钟**（含下载依赖），之后快很多。

---

## ⚠️ 三个必踩的坑

### 1. 必须用 JDK 17

项目用 **Gradle 8.5**，它最高支持 **Java 21**。

本机 Android Studio 自带的 `D:/Android Studio/jbr` 是 **JDK 25**，
直接构建会失败（报 `What went wrong: 25.0.3`）。

**所以必须显式指定 JDK 17**，不要依赖 PATH 里的 `java`。

### 2. 代理必须是「全局模式」

构建需要访问 **`dl.google.com`**（AGP、Compose、Android platform 34 全在上面）。

走**分流 / 规则模式**时它会被拦成 `CONNECT tunnel failed, response 502`；
切成**全局模式**后正常，而且速度从 110KB/s 涨到 6.3MB/s。

**构建失败先查这个。**

### 3. 签名不同，覆盖安装会失败

本机的 debug keystore（`~/.android/debug.keystore`）是 **2026-09-23 新建的**，
而仓库里那份旧 APK 是**另一台机器**构建的 —— 两者签名不同。

直接安装会报：

```
INSTALL_FAILED_UPDATE_INCOMPATIBLE
```

**必须先卸载旧 App。**

> ⚠️ 卸载会**清空 App 本地的 SharedPreferences**，也就是本机缓存的聊天记录
> 和全部设置。服务端数据库里的对话是完整的，但客户端缓存会丢。

---

## 安装后配置

在 App 的**设置页**填写：

| 项 | 说明 |
|----|------|
| 服务器 IP | PC 端服务地址 |
| 端口 | 默认 8080 |
| 启用公网地址 / 公网地址 | 走 Cloudflare Tunnel 时用 |
| **访问令牌（可选）** | **如果服务端设了 `api_token`，这里必须填同一个值**，否则 WebSocket 会被拒 |
| 启动时自动连接 | 打开 App 自动连 |
| 测试服务器连通性 | 排查配置问题 |

---

## 已知限制

**聊天页底部的 3 个快捷操作按钮（图片 / 语音 / 常用语）是占位**，
点了不会生效（`ChatScreen.kt` 里是 `TODO`）。需要平台权限 + 真机调试才能补。

---

## 版本信息

| 项 | 值 |
|----|----|
| applicationId | `com.elysia.ai` |
| compileSdk | 34 |
| minSdk | 26 |
| targetSdk | 31（刻意降低，匹配 HarmonyOS 4.2 的 AOSP 12 兼容层） |
| Gradle | 8.5 |
