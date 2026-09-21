# Android Studio 部署 Qwen-7B 完整指南

本指南详细介绍如何在 Android Studio 中构建和运行 Qwen-7B Chat 应用。

## 前置要求

### 1. 硬件要求
- **电脑**: Windows 10/11, macOS 11+, 或 Linux
- **内存**: 至少 16GB RAM
- **磁盘**: 至少 20GB 可用空间
- **Android 手机**: Android 10+, 8GB+ RAM, arm64-v8a 架构

### 2. 软件要求
- **Android Studio**: Arctic Fox (2020.3.1) 或更高版本
- **JDK**: 11 或更高版本
- **Android SDK**: API 级别 24+
- **NDK**: 25.0 或更高版本
- **CMake**: 3.22.1 或更高版本
- **Python**: 3.8+ (用于模型转换)

---

## 第一阶段：准备模型文件

### 步骤 1: 下载 Qwen-7B 模型

```bash
# 运行模型下载脚本
python download_qwen_model.py
```

模型将被下载到 `models/qwen/Qwen-7B-Chat-Int4/` 目录。

### 步骤 2: 转换模型为 GGUF 格式

```bash
# 方式 A: 简单转换（需要已编译 llama.cpp）
python convert_to_gguf.py --model models/qwen/Qwen-7B-Chat-Int4 --output models/gguf --quantize q4_0

# 方式 B: 手动转换
# 1. 进入 llama.cpp 目录
cd android_app/app/llama.cpp

# 2. 转换模型（注意相对路径：需要上溯 3 层才回到项目根目录）
python convert_hf_to_gguf.py ../../../models/qwen/Qwen-7B-Chat-Int4 --outfile ../../../models/gguf/qwen-7b-chat-f16.gguf

# 3. 量化模型（需要先编译）
./build/bin/llama-quantize ../../../models/gguf/qwen-7b-chat-f16.gguf ../../../models/gguf/qwen-7b-chat-q4_0.gguf q4_0
```

### 步骤 3: 把模型推到手机外部存储

**本应用不使用 `assets/` 目录**，而是从手机外部存储读取 `.gguf` 文件。

```bash
# 直接推送到手机 /sdcard/
adb push models/gguf/qwen-7b-chat-q4_0.gguf /sdcard/
```

Android 11（API 30）及以上，读取 `/sdcard` 下的任意文件需要「所有文件访问权限」，
应用首次启动时会自动跳转到系统设置页，请手动打开该开关。

---

## 第二阶段：配置 Android Studio 项目

### 步骤 1: 打开项目

1. 启动 Android Studio
2. 选择 `Open` 
3. 导航到 `android_app` 目录并打开

### 步骤 2: 同步 Gradle

1. 首次打开项目后，Android Studio 会提示同步 Gradle
2. 点击 `Sync Now`
3. 等待依赖下载完成

### 步骤 3: 检查 NDK 和 CMake

1. 打开 `File` → `Settings` → `Appearance & Behavior` → `System Settings` → `Android SDK`
2. 切换到 `SDK Tools` 标签
3. 确保已安装:
   - **NDK (Side by side)**: 版本 25.0 或更高
   - **CMake**: 版本 3.22.1 或更高
   - **Android SDK Build-Tools**

如果没有安装，勾选并点击 `Apply`。

### 步骤 4: 配置 Build Variants

1. 打开 `View` → `Tool Windows` → `Build Variants`
2. 选择 `debug` 构建变体

---

## 第三阶段：编译和运行

### 步骤 1: 编译项目

1. 选择 `Build` → `Make Project`
2. 等待编译完成
3. 如果出错，查看错误信息并修复

### 常见编译问题

#### 问题 1: NDK 找不到

```
Error: NDK not configured
```

**解决方案**: 
- 在 SDK Manager 中安装 NDK
- 或在 `local.properties` 中添加:
  ```properties
  ndk.dir=C\\:\\Users\\YourUser\\AppData\\Local\\Android\\Sdk\\ndk\\25.0.8775105
  ```

#### 问题 2: CMake 错误

```
Error: CMake not found
```

**解决方案**:
- 在 SDK Manager 中安装 CMake
- 或在 `local.properties` 中添加:
  ```properties
  cmake.dir=C\\:\\Users\\YourUser\\AppData\\Local\\Android\\Sdk\\cmake\\3.22.1
  ```

#### 问题 3: llama.cpp 编译错误

```
error: no member named 'xxx' in namespace 'std'
```

**解决方案**:
- 确保 C++17 标准已启用
- 检查 `CMakeLists.txt` 中的配置

### 步骤 2: 连接设备

**方式 A: 使用真机**

1. 在手机上启用开发者选项:
   - 设置 → 关于手机 → 连续点击"构建号"7 次
2. 启用 USB 调试:
   - 设置 → 开发者选项 → USB 调试
3. 通过 USB 连接电脑
4. 在手机上允许 USB 调试

**方式 B: 使用模拟器**

1. 打开 `Device Manager`
2. 创建或选择一个设备
3. 点击启动

**注意**: 模拟器可能不支持 AI 模型推理，建议使用真机。

### 步骤 3: 运行应用

1. 选择目标设备
2. 点击绿色运行按钮 (▶)
3. 或选择 `Run` → `Run 'app'`
4. 等待应用安装并启动

---

## 第四阶段：测试和调试

### 测试流程

1. **等待模型加载**
   - 应用启动后会显示"正在加载模型..."
   - 首次加载可能需要 1-3 分钟

2. **开始对话**
   - 模型加载完成后，状态栏会显示"模型加载完成"
   - 在输入框中输入消息
   - 点击"发送"按钮

3. **查看响应**
   - 助手回复会逐字显示（流式生成）
   - 回复完成后可以继续对话

### 查看日志

1. 打开 `Logcat`:
   - `View` → `Tool Windows` → `Logcat`
2. 过滤标签:
   - 输入 `LlamaEngine` 或 `MainActivity`
3. 查看调试信息

### 常见问题

#### Q1: 应用启动后立即崩溃

**可能原因**:
- 模型文件缺失
- 内存不足
- 设备架构不兼容

**解决方案**:
```bash
# 检查模型文件是否已推到手机外部存储
adb shell ls -la /sdcard/

# 检查设备架构
adb shell getprop ro.product.cpu.abi
# 应该输出：arm64-v8a
```

#### Q2: 模型加载失败

**错误信息**: "模型加载失败"（应用内状态栏）

**解决方案**:
1. 确认 GGUF 文件已放到手机外部存储（`/sdcard/` 等），**不是** APK 的 assets 目录
2. 确认已在系统设置中授予「所有文件访问权限」（Android 11+）
3. 检查文件大小是否完整（q4_0 约 4GB）

#### Q3: 推理速度太慢

**优化方案**:
1. 使用更小的量化级别 (q3_K, q4_0)
2. 减少上下文长度
3. 关闭其他应用释放内存

#### Q4: 内存不足

**解决方案**:
1. 关闭其他应用
2. 使用更小的模型 (Qwen-1.8B)
3. 重启应用释放内存

---

## 第五阶段：构建 Release APK

### 步骤 1: 生成签名密钥（首次）

```bash
keytool -genkey -v -keystore qwenchat.keystore -keyalg RSA -keysize 2048 -validity 10000 -alias qwenchat
```

### 步骤 2: 构建 APK

**方式 A: 使用菜单**

1. `Build` → `Generate Signed Bundle / APK`
2. 选择 `APK`
3. 选择签名密钥
4. 选择 `release` 构建变体
5. 点击 `Finish`

**方式 B: 使用 Gradle**

```bash
cd android_app
./gradlew assembleRelease
```

### 步骤 3: 安装 APK

```bash
adb install app/build/outputs/apk/release/app-release.apk
```

---

## 性能调优

### 调整推理参数

编辑 `android_app/app/src/main/cpp/llama-helper.cpp` 中 `nativeLoadModel()` 里的 `g_state.params`：

```cpp
// 调整上下文大小（代码中实际默认值就是 2048）
g_state.params.n_ctx = 2048;

// 调整批处理大小（代码中实际默认值 512）
g_state.params.n_batch = 512;

// 调整线程数
g_state.params.n_threads = 4;  // 根据设备 CPU 核心数调整
```

### 内存优化

```gradle
// 在 build.gradle 中添加
android {
    defaultConfig {
        ndk {
            abiFilters 'arm64-v8a'  // 只打包 64 位版本
        }
    }
    
    packagingOptions {
        jniLibs {
            useLegacyPackaging false
        }
    }
}
```

---

## 参考资源

- [llama.cpp 官方文档](https://github.com/ggerganov/llama.cpp)
- [Android NDK 文档](https://developer.android.com/ndk/guides)
- [Qwen 官方文档](https://github.com/QwenLM/Qwen)
- [Kotlin 协程文档](https://kotlinlang.org/docs/coroutines-overview.html)

---

## 故障排查清单

- [ ] 模型文件已下载到 `models/qwen/Qwen-7B-Chat-Int4/`
- [ ] GGUF 模型已转换并推送到手机外部存储（`/sdcard/`），**不是** `assets/`
- [ ] 已在系统设置中授予「所有文件访问权限」（Android 11+）
- [ ] NDK 和 CMake 已安装
- [ ] Gradle 同步成功
- [ ] 设备已连接并启用 USB 调试
- [ ] Logcat 中没有严重错误
- [ ] 设备有足够的可用内存

---

## 下一步

1. ✅ 完成模型转换
2. ✅ 配置 Android Studio 项目
3. ✅ 编译并测试应用
4. ⬜ 优化性能和用户体验
5. ⬜ 发布到应用商店（可选）
