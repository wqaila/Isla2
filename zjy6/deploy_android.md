# Android 手机部署 Qwen-7B 完整指南

## 概述
本指南详细介绍如何将 Qwen-7B 模型部署到 Android 手机，实现离线运行。

## 硬件要求
- **Android 手机**: 8GB+ RAM
- **存储空间**: 至少 6GB 可用空间
- **Android 版本**: Android 10+
- **架构**: ARM64-v8a (主流手机)

## 方案选择

### 推荐方案：llama.cpp + GGUF
- 纯离线运行
- 4bit 量化后约 4GB
- 推理速度 3-5 tokens/s
- 学习资源丰富

---

## 第一阶段：PC 端准备

### 1. 安装依赖

本仓库**没有** `requirements.txt`，请直接安装所需的 Python 包：

```bash
# 模型转换 / 量化所需依赖
pip install torch transformers sentencepiece protobuf numpy absl-py

# 下载模型还需要 modelscope
pip install modelscope

# 本项目 Android 端已内置 llama.cpp 源码（android_app/app/llama.cpp），无需另行克隆
```

### 2. 下载 Qwen-7B 模型

```bash
# 下载到 models/qwen/Qwen-7B-Chat-Int4/（脚本已内置国内镜像，不接受命令行参数）
python download_qwen_model.py
```

> 注意：脚本名是 `download_qwen_model.py`（不是 `download_model.py`），
> 且它没有 `--model` / `--output` 参数，下载目录固定为 `models/qwen/Qwen-7B-Chat-Int4/`。

### 3. 转换为 GGUF 格式

```bash
# 注意：convert_to_gguf.py 没有 --download 选项，必须传入本地模型目录
python convert_to_gguf.py --model models/qwen/Qwen-7B-Chat-Int4 --output models/gguf --quantize q4_0
```

> `convert_to_gguf.py` 依赖 `android_app/app/llama.cpp/convert_hf_to_gguf.py`，
> 请确认该目录存在（本项目已内置）。

### 4. 验证 GGUF 文件

```bash
# 检查文件大小（大小取决于量化级别）
ls -lh ./models/gguf/*.gguf
```

---

## 第二阶段：Android 端部署

> **注意**：本节「方案 A」描述的是 llama.cpp 官方 `examples/android` 示例工程，
> 与本仓库自带的 `android_app/` **不是同一个应用**。
> 如果要部署本仓库的应用，请直接看
> [android_android_studio_guide.md](android_android_studio_guide.md)。
> 关键差异：本仓库的应用从**手机外部存储**（如 `/sdcard/`）读取 `.gguf`，
> 不使用 APK 的 `assets/` 目录。

### 方案 A: 使用 llama.cpp Android 示例项目

#### 步骤 1: 克隆项目

```bash
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
```

#### 步骤 2: 准备模型文件

```bash
# 将 GGUF 模型复制到 Android 项目
cp ../models/quantized/qwen-7b-chat-q4_0.gguf examples/android/app/src/main/assets/
```

#### 步骤 3: 配置 Android 项目

编辑 `examples/android/app/build.gradle`:

```gradle
android {
    defaultConfig {
        ndk {
            abiFilters 'arm64-v8a'  // 仅支持 64 位 ARM
        }
    }
}
```

#### 步骤 4: 修改推理代码

编辑 `examples/android/app/src/main/java/ai/llama/example/MainActivity.java`:

```java
public class MainActivity extends AppCompatActivity {
    private TextView chatOutput;
    private EditText userInput;
    private Button sendButton;
    private LlamaModel llamaModel;
    
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        
        chatOutput = findViewById(R.id.chatOutput);
        userInput = findViewById(R.id.userInput);
        sendButton = findViewById(R.id.sendButton);
        
        // 加载模型
        new LoadModelTask().execute();
        
        sendButton.setOnClickListener(v -> {
            String prompt = userInput.getText().toString();
            new GenerateTask().execute(prompt);
            userInput.setText("");
        });
    }
    
    private class LoadModelTask extends AsyncTask<Void, Void, Boolean> {
        @Override
        protected Boolean doInBackground(Void... voids) {
            llamaModel = new LlamaModel();
            return llamaModel.loadModel(getAssets(), "qwen-7b-chat-q4_0.gguf");
        }
        
        @Override
        protected void onPostExecute(Boolean success) {
            if (success) {
                chatOutput.append("模型加载成功!\n");
            } else {
                chatOutput.append("模型加载失败!\n");
            }
        }
    }
    
    private class GenerateTask extends AsyncTask<String, String, Void> {
        @Override
        protected Void doInBackground(String... prompts) {
            String response = llamaModel.generate(prompts[0], 512);
            publishProgress(response);
            return null;
        }
        
        @Override
        protected void onProgressUpdate(String... responses) {
            chatOutput.append("Assistant: " + responses[0] + "\n");
        }
    }
}
```

#### 步骤 5: 布局文件

编辑 `examples/android/app/src/main/res/layout/activity_main.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:layout_width="match_parent"
    android:layout_height="match_parent"
    android:orientation="vertical">

    <ScrollView
        android:layout_width="match_parent"
        android:layout_height="0dp"
        android:layout_weight="1">

        <TextView
            android:id="@+id/chatOutput"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:padding="16dp"
            android:textSize="14sp" />
    </ScrollView>

    <LinearLayout
        android:layout_width="match_parent"
        android:layout_height="wrap_content"
        android:orientation="horizontal"
        android:padding="8dp">

        <EditText
            android:id="@+id/userInput"
            android:layout_width="0dp"
            android:layout_height="wrap_content"
            android:layout_weight="1"
            android:hint="输入消息..." />

        <Button
            android:id="@+id/sendButton"
            android:layout_width="wrap_content"
            android:layout_height="wrap_content"
            android:text="发送" />
    </LinearLayout>
</LinearLayout>
```

#### 步骤 6: 构建 APK

```bash
# 使用 Gradle 构建
./gradlew assembleDebug

# APK 位置：examples/android/app/build/outputs/apk/debug/app-debug.apk
```

#### 步骤 7: 安装到手机

```bash
# 连接手机并安装
adb install examples/android/app/build/outputs/apk/debug/app-debug.apk

# 或者使用 Android Studio 直接运行
```

---

## 方案 B: 使用 MLC-LLM (性能更优)

### 步骤 1: 克隆 MLC-LLM

```bash
git clone https://github.com/mlc-ai/mlc-llm
cd mlc-llm
```

### 步骤 2: 安装依赖

```bash
pip install -e .
pip install mlc-ai-nightly-cu118 mlc-llm-nightly-cu118
```

### 步骤 3: 编译模型

```bash
# 配置编译参数
export MLC_LLM_SOURCE_DIR=/path/to/mlc-llm

# 编译 Qwen-7B
python -m mlc_llm build \
    --model Qwen/Qwen-7B-Chat \
    --quantization q4f16_1 \
    --output dist/Qwen-7B-Chat-q4f16_1-MLC
```

### 步骤 4: 打包 Android App

```bash
# 使用 MLC Chat Android 项目
git clone https://github.com/mlc-ai/mlc-chat-android

# 复制编译的模型
cp -r dist/Qwen-7B-Chat-q4f16_1-MLC mlc-chat-android/app/src/main/assets/
```

### 步骤 5: 构建和安装

```bash
cd mlc-chat-android
./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

---

## 第三阶段：优化和调试

### 性能优化

#### 1. 调整推理参数

> 下面这段 `LlamaConfig` 是 **llama.cpp 官方示例工程**里的写法，本仓库没有这个类。
> 本仓库应用（`android_app/`）的推理参数在
> `android_app/app/src/main/cpp/llama-helper.cpp` 的 `nativeLoadModel()` 里直接设置
> `g_state.params.n_ctx` / `n_batch` / `n_threads`（当前默认 n_ctx=2048、n_batch=512、n_threads=4）。

```java
// 在代码中调整
LlamaConfig config = new LlamaConfig();
config.setNContext(512);      // 减少上下文长度
config.setNBatch(256);        // 减少批处理大小
config.setNThreads(4);        // 使用 4 个线程
config.setUseMmap(true);      // 使用内存映射
```

#### 2. 内存优化

```bash
# 在手机上增加 swap (需要 root)
adb shell
su
fallocate -l 2G /data/swapfile
mkswap /data/swapfile
swapon /data/swapfile
```

### 常见问题

#### Q1: 模型加载失败
**解决方案**:
- 检查 GGUF 文件是否完整
- 确认手机架构是 arm64-v8a
- 检查可用存储空间

#### Q2: 推理速度太慢
**解决方案**:
- 减少上下文长度 (n_ctx)
- 使用更小的量化 (q4_0)
- 考虑使用 Qwen-1.8B

#### Q3: 内存不足
**解决方案**:
- 关闭其他应用
- 使用更小的模型
- 增加 swap 空间

---

## 参考资源

- [llama.cpp GitHub](https://github.com/ggerganov/llama.cpp)
- [llama.cpp Android 示例](https://github.com/ggerganov/llama.cpp/tree/master/examples/android)
- [MLC-LLM 文档](https://llm.mlc.ai/docs/)
- [Qwen 官方文档](https://github.com/QwenLM/Qwen)

---

## 下一步

1. 完成 PC 端模型转换
2. 配置 Android 开发环境
3. 构建并测试 APK
4. 优化性能和用户体验
