# zjy6 —— Android 应用（Kotlin + JNI）

把 llama.cpp 跑在手机上：**模型和推理全在本机**，不需要连服务器。

> 构建步骤见上一级 [`../README.md`](../README.md) 的「方案一：Android 手机部署」。

---

## ⚠️ 目录结构不直观

这个目录**内嵌了一份 llama.cpp 源码**，真正的 Android 工程藏在它里面：

```text
android_app/
├── app/
│   └── llama.cpp/                                  ← 内嵌的 llama.cpp 源码
│       └── examples/llama.android/
│           ├── app/                                ← Android 应用（Kotlin）
│           │   └── src/main/java/com/example/llama/
│           │       ├── MainActivity.kt
│           │       └── MessageAdapter.kt
│           └── lib/
│               └── src/main/java/com/arm/aichat/   ← 推理封装
│                   ├── AiChat.kt
│                   ├── InferenceEngine.kt
│                   └── gguf/                       ← GGUF 元数据读取
└── gradle-8.2/                                     ← Gradle 发行包（**不入库**，137MB）
```

所以**构建目录是 `android_app/`，不是 `android_app/app/`**，别找错。

---

## 两个包名

| 包名 | 内容 |
|------|------|
| `com.example.llama` | 界面层（Activity、消息列表） |
| `com.arm.aichat` | 推理层（引擎、GGUF 解析） |

后者是 llama.cpp 官方示例自带的包名，没有改名。

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
| `models/` | 模型权重 |
| `build/`、`.gradle/`、`.cxx/`、`.idea/` | 构建产物与 IDE 配置 |
