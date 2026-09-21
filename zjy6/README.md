# Qwen-7B 本地部署项目

本项目提供 Qwen-7B 模型的本地部署方案，目前已实现：

- **Android 手机端**：Kotlin UI + llama.cpp/JNI 离线推理（模型从手机外部存储读取）
- **PC 端**：直接使用 llama.cpp 命令行推理

> 未实现/规划中：RK3568 开发板部署、从 APK 内置 `assets/` 加载模型。仓库中没有对应实现，请勿按这两种方式操作。

## 项目结构

```
zjy6/
├── android_app/                   # Android 应用（Kotlin + JNI）
│   ├── build.gradle               # 项目级构建配置
│   ├── settings.gradle
│   ├── gradle.properties
│   ├── local.properties           # 本机 SDK 路径（已被 .gitignore 排除，请勿提交）
│   ├── app/
│   │   ├── build.gradle           # 应用级构建配置
│   │   ├── CMakeLists.txt         # CMake 配置 (JNI)
│   │   ├── proguard-rules.pro     # release 混淆规则
│   │   ├── llama.cpp/             # llama.cpp 源码（第三方，已被 .gitignore 排除）
│   │   └── src/main/
│   │       ├── AndroidManifest.xml
│   │       ├── java/com/example/qwenchat/
│   │       │   ├── MainActivity.kt      # 主界面 / 权限申请 / prompt 拼接
│   │       │   └── LlamaEngine.kt       # 推理引擎封装
│   │       └── cpp/
│   │           └── llama-helper.cpp     # JNI 辅助代码
│
├── models/                        # 模型目录（已被 .gitignore 排除）
│   ├── gguf/merged_model/
│   │   └── model-f16.gguf              # 已转换的 GGUF 模型（约 8.7GB）
│   ├── qwen/Qwen-7B-Chat-Int4/         # Qwen-7B-Chat-Int4 原始权重（下载脚本的目标目录）
│   ├── google/gemma-4-E2B-it/          # Gemma 基模型（仅用于 LoRA 微调，不是可直接部署的 GGUF）
│   └── lora_output/                    # LoRA 微调输出
│
├── pc_server/                     # 预留目录（当前为空）
│
├── convert_to_gguf.py             # GGUF 转换工具
├── merge_and_convert_model.py     # LoRA 合并 + GGUF 转换 + 量化工具
├── merge_lora_only.py             # 仅 LoRA 合并工具
├── download_qwen_model.py         # 模型下载工具
├── token_truncation_tool.py       # Token 截断工具
│
├── merge_and_convert_model.md     # 合并转换工具使用指南
├── deploy_android.md              # Android 部署指南
├── android_android_studio_guide.md # Android Studio 部署指南
├── .gitignore
└── README.md                      # 项目说明
```

## 快速开始

### 方案一：Android 手机部署（推荐）

适合：移动设备、离线使用、学习实践

#### 1. 准备 GGUF 模型

仓库中已有的 GGUF 模型：

```bash
# 文件位置: models/gguf/merged_model/model-f16.gguf（约 8.7GB）
```

如需从 HuggingFace 模型重新转换：

```bash
# 转换 + 量化
python merge_and_convert_model.py --convert --model models/qwen/Qwen-7B-Chat-Int4 --quantize q8_0

# 仅量化已有 F16 模型（需先编译 llama.cpp）
cd android_app/app/llama.cpp
./build/bin/llama-quantize ../../../models/gguf/merged_model/model-f16.gguf ../../../models/gguf/merged_model/model-q8_0.gguf q8_0
```

#### 2. 把模型推到手机

应用从**手机外部存储**读取 `.gguf`（不是 APK 的 assets）：

```bash
adb push models/gguf/merged_model/model-f16.gguf /sdcard/
```

#### 3. 构建并安装

```bash
cd android_app
./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

#### 4. 首次启动授权

Android 11 及以上读取 `/sdcard` 下的任意文件需要「所有文件访问权限」，
应用启动时会跳转到系统设置页，请手动打开该开关，返回应用后会自动开始加载模型。

详细指南：[deploy_android.md](deploy_android.md)

### 方案二：PC 端推理

适合：开发调试、性能测试

```bash
# 使用 llama.cpp 进行推理
cd android_app/app/llama.cpp
./build/bin/llama-cli.exe -m ../../../models/gguf/merged_model/model-f16.gguf -p "你好" -n 128
```

## 方案对比

| 特性 | Android 手机 | PC 端推理 |
|------|-------------|----------|
| 推理速度 | 3-5 tokens/s | 10-20 tokens/s |
| 内存需求 | 8GB+ | 8GB+ |
| 离线能力 | ✅ | ✅ |
| 便携性 | ✅ | ❌ |

## 硬件要求

### Android 手机
- Android 10+（Android 11 及以上需要授予「所有文件访问权限」，否则读不到 /sdcard 下的模型）
- 8GB+ RAM
- 模型放在**外部存储**（如 `/sdcard/`），预留 10GB+ 可用空间
- ARM64-v8a 架构

### PC 端
- 8GB+ RAM
- 10GB+ 可用空间

## 模型量化选项

> 下表大小为粗略参考，实际大小取决于模型参数量，请以量化后的实际文件为准。

| 量化级别 | 参考大小 | 速度 | 质量 |
|----------|----------|------|------|
| F16 | 约 8.7GB（仓库中 `models/gguf/merged_model/model-f16.gguf` 实测） | 慢 | 最高 |
| Q8_0 | 约为 F16 的一半 | 中 | 高 |
| Q4_1 | 约为 F16 的 1/4 | 快 | 中 |
| Q4_0 | 约为 F16 的 1/4 | 最快 | 中低 |

**推荐**: Q8_0（平衡速度和质量）

## 常见问题

### Q: 模型加载失败？
A: 检查模型文件完整性，确认存储空间充足；并确认已把 `.gguf` 放到 `/sdcard/` 且已授予「所有文件访问权限」（Android 11+）。

### Q: 推理速度太慢？
A: 尝试更小的量化级别 (Q4_1)，减少上下文长度。

### Q: 内存不足？
A: 关闭其他应用，使用更小的模型或更低的量化级别。

## 参考资源

- [Qwen 官方仓库](https://github.com/QwenLM/Qwen)
- [llama.cpp](https://github.com/ggerganov/llama.cpp)
- [Android NDK](https://developer.android.com/ndk)

## 许可证

MIT License
