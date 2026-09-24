# zjy6 —— Android 应用（Kotlin + JNI）

把 llama.cpp 跑在手机上：**模型和推理全在本机**，不需要连服务器。

> 构建步骤见上一级 [`../README.md`](../README.md) 的「方案一：Android 手机部署」。

---

## 目录结构

```text
android_app/
├── settings.gradle              rootProject.name = "qwenchat"，include ':app'
├── build.gradle                 顶层构建配置
├── app/                         ← **真正被构建的模块**
│   ├── build.gradle             namespace / applicationId = com.example.qwenchat
│   ├── CMakeLists.txt           add_subdirectory(llama.cpp) + add_library(llama-helper)
│   ├── src/main/cpp/
│   │   └── llama-helper.cpp     ← JNI 胶水层（Kotlin ↔ llama.cpp）
│   ├── src/main/java/com/example/qwenchat/
│   │   ├── MainActivity.kt      ← 界面与交互
│   │   └── LlamaEngine.kt       ← 推理调用封装
│   ├── llama.cpp/               ← 第三方源码依赖（由 CMake 引入，非本仓库编写）
│   └── llama.cpp.zip            ← llama.cpp 压缩包（34MB，冗余，不入库）
└── gradle-8.2/                  ← Gradle 发行包本体（不入库，137MB）
```

**构建目录是 `android_app/`**（有 `settings.gradle` 的那一层），不是 `android_app/app/`。

---

## ⚠️ 一个容易看错的地方

`app/llama.cpp/examples/llama.android/` 里**也有一套 Android 工程**
（包名 `com.example.llama` + `com.arm.aichat`），但那是
**llama.cpp 官方自带的上游示例**：

- 它有自己的 `settings.gradle.kts`，是**独立工程**
- **不参与本项目的构建**（`android_app/settings.gradle` 只 `include ':app'`）
- 本项目**没有**基于它改造

本项目实际构建的是 **`com.example.qwenchat`**（`android_app/app/`），
用的是自己写的 `LlamaEngine.kt` + `llama-helper.cpp`，
只把 `app/llama.cpp/` 当作**源码依赖**通过 CMake 的 `add_subdirectory` 引入。

> 本 README 的早期版本把这两者写反了（说真正工程在上游示例里），
> 已于 **2026-09-24 更正**。

---

## 模型放哪

需要自己准备 GGUF 模型（**不入库**），按上一级 README 的说明放到
`models/gguf/` 下。默认找的是合并后的 F16 模型（约 8.7GB），
也可以用 `convert_to_gguf.py` 自己转换 / 量化。

---

## 不入库的内容

| 路径 | 说明 |
|------|------|
| `android_app/gradle-8.2/` | Gradle 8.2 发行包本体（137MB 第三方产物） |
| `android_app/app/llama.cpp.zip` | llama.cpp 压缩包（34MB，与 `llama.cpp/` 重复） |
| `models/` | 模型权重 |
| `build/`、`.gradle/`、`.cxx/`、`.idea/` | 构建产物与 IDE 配置 |
