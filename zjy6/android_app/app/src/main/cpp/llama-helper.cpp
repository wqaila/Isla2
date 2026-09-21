#include <jni.h>
#include <string>
#include <vector>
#include <cstring>
#include <algorithm>
#include <mutex>
#include <sys/stat.h>
#include <unistd.h>
#include <android/log.h>
#include <llama.h>
#include <fstream>
#include <iostream>

#define LOG_TAG "LlamaEngine"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

// GGUF 相关常量
#define GGUF_MAGIC "GGUF"
#define GGUF_VERSION 3

// 验证 GGUF 文件
static bool validateGGUFFile(const char* path) {
    FILE* file = fopen(path, "rb");
    if (!file) {
        LOGE("Cannot open file: %s", path);
        return false;
    }
    
    // 读取 magic number
    char magic[4];
    if (fread(magic, 1, 4, file) != 4) {
        LOGE("Cannot read magic number from: %s", path);
        fclose(file);
        return false;
    }
    
    // 检查 magic number
    if (memcmp(magic, GGUF_MAGIC, 4) != 0) {
        LOGE("Invalid GGUF magic number. File may not be a valid GGUF file");
        LOGE("Got: %.4s, expected: GGUF", magic);
        fclose(file);
        return false;
    }
    
    // 读取版本
    uint32_t version;
    if (fread(&version, sizeof(version), 1, file) != 1) {
        LOGE("Cannot read version from: %s", path);
        fclose(file);
        return false;
    }
    
    LOGI("GGUF file validated: magic=GGUF, version=%u", version);
    
    if (version == 1) {
        LOGE("GGUF version 1 is no longer supported");
        fclose(file);
        return false;
    }
    
    if (version > GGUF_VERSION) {
        LOGE("GGUF version %u is not supported, only up to version %d", version, GGUF_VERSION);
        fclose(file);
        return false;
    }
    
    fclose(file);
    return true;
}

// 存储上下文指针
static struct LlamaState {
    llama_context* ctx = nullptr;
    llama_model* model = nullptr;
    llama_sampler* sampler = nullptr;
    llama_context_params params;
} g_state;

// 保护 g_state 的互斥锁：加载/推理/释放可能来自不同线程
static std::mutex g_state_mutex;

// 释放当前持有的模型/上下文/采样器。
// 注意：调用方必须已经持有 g_state_mutex。
static void freeStateLocked() {
    if (g_state.sampler) {
        llama_sampler_free(g_state.sampler);
        g_state.sampler = nullptr;
    }
    
    if (g_state.ctx) {
        llama_free(g_state.ctx);
        g_state.ctx = nullptr;
    }
    
    if (g_state.model) {
        llama_model_free(g_state.model);
        g_state.model = nullptr;
    }
}

// 前向声明函数
static jlong nativeLoadModel(JNIEnv* env, jobject thiz, jstring modelPath);
static jstring nativeGenerate(JNIEnv* env, jobject thiz, jlong ctxPtr, jstring prompt, jint maxTokens);
static void nativeReset(JNIEnv* env, jobject thiz, jlong ctxPtr);
static void nativeFree(JNIEnv* env, jobject thiz, jlong ctxPtr);
static jstring nativeGetModelInfo(JNIEnv* env, jobject thiz, jlong ctxPtr);

// Java 方法签名
static JNINativeMethod methods[] = {
    {"nativeLoadModel", "(Ljava/lang/String;)J", (void*)nativeLoadModel},
    {"nativeGenerate", "(JLjava/lang/String;I)Ljava/lang/String;", (void*)nativeGenerate},
    {"nativeReset", "(J)V", (void*)nativeReset},
    {"nativeFree", "(J)V", (void*)nativeFree},
    {"nativeGetModelInfo", "(J)Ljava/lang/String;", (void*)nativeGetModelInfo},
};

// 加载模型
static jlong nativeLoadModel(JNIEnv* env, jobject thiz, jstring modelPath) {
    // 整个加载过程加锁，避免与推理/释放并发操作 g_state
    std::lock_guard<std::mutex> lock(g_state_mutex);
    
    const char* path = env->GetStringUTFChars(modelPath, nullptr);
    LOGI("Loading model from: %s", path);
    
    // 重复调用时先释放上一次的 model/ctx/sampler，否则会内存泄漏
    freeStateLocked();
    
    // 检查文件是否存在
    struct stat st;
    if (stat(path, &st) != 0) {
        LOGE("File not found or cannot access: %s", path);
        env->ReleaseStringUTFChars(modelPath, path);
        return 0;
    }
    LOGI("File size: %lld bytes", (long long)st.st_size);
    
    // 验证 GGUF 文件格式
    LOGI("Starting GGUF validation...");
    if (!validateGGUFFile(path)) {
        LOGE("GGUF validation failed");
        env->ReleaseStringUTFChars(modelPath, path);
        return 0;
    }
    LOGI("GGUF validation passed");
    
    // 尝试读取文件前 100 字节来确认文件可读
    FILE* testFile = fopen(path, "rb");
    if (testFile) {
        char buffer[100];
        size_t bytesRead = fread(buffer, 1, 100, testFile);
        fclose(testFile);
        LOGI("Successfully read %zu bytes from file", bytesRead);
    } else {
        LOGE("Cannot open file for reading: %s", path);
        env->ReleaseStringUTFChars(modelPath, path);
        return 0;
    }
    
    // 获取可用内存信息
    FILE* meminfo = fopen("/proc/meminfo", "r");
    if (meminfo) {
        char line[256];
        long totalMemKB = 0;
        long freeMemKB = 0;
        long buffersKB = 0;
        long cachedKB = 0;
        long slabKB = 0;
        while (fgets(line, sizeof(line), meminfo)) {
            if (strncmp(line, "MemTotal:", 9) == 0) {
                sscanf(line + 9, "%ld", &totalMemKB);
            }
            if (strncmp(line, "MemAvailable:", 13) == 0) {
                sscanf(line + 13, "%ld", &freeMemKB);
            }
            if (strncmp(line, "Buffers:", 8) == 0) {
                sscanf(line + 8, "%ld", &buffersKB);
            }
            if (strncmp(line, "Cached:", 7) == 0) {
                sscanf(line + 7, "%ld", &cachedKB);
            }
            if (strncmp(line, "Slab:", 5) == 0) {
                sscanf(line + 5, "%ld", &slabKB);
            }
        }
        fclose(meminfo);
        LOGI("=== Memory Info ===");
        LOGI("Total RAM: %ld MB (%ld MB)", totalMemKB / 1024, totalMemKB);
        LOGI("Available: %ld MB (%ld KB)", freeMemKB / 1024, freeMemKB);
        LOGI("Buffers: %ld KB", buffersKB);
        LOGI("Cached: %ld KB", cachedKB);
        LOGI("Slab: %ld KB", slabKB);
        LOGI("===================");
        
        // 检查 Android 内存限制
        FILE* lmk = fopen("/proc/sys/vm/min_free_kbytes", "r");
        if (lmk) {
            long minFree = 0;
            if (fscanf(lmk, "%ld", &minFree) == 1) {
                LOGI("Android min_free_kbytes: %ld KB (%ld MB)", minFree, minFree / 1024);
            }
            fclose(lmk);
        }
        
        if (freeMemKB < 4000000) {  // 少于 4GB 可用内存
            LOGE("Warning: Low available memory (%ld MB)", freeMemKB / 1024);
            LOGE("This may be due to Android system memory management");
        }
    }
    
    // 尝试使用 Android ActivityManager 获取内存信息
    LOGI("Checking process memory limit...");
    FILE* status = fopen("/proc/self/status", "r");
    if (status) {
        char line[256];
        while (fgets(line, sizeof(line), status)) {
            if (strncmp(line, "VmSize:", 7) == 0) {
                long vmSizeKB = 0;
                sscanf(line + 7, "%ld", &vmSizeKB);
                LOGI("Process VmSize: %ld KB (%ld MB)", vmSizeKB, vmSizeKB / 1024);
            }
            if (strncmp(line, "VmRSS:", 6) == 0) {
                long vmRSSKB = 0;
                sscanf(line + 6, "%ld", &vmRSSKB);
                LOGI("Process VmRSS: %ld KB (%ld MB)", vmRSSKB, vmRSSKB / 1024);
            }
        }
        fclose(status);
    }
    
    // 初始化上下文参数 - 优化性能配置
    g_state.params = llama_context_default_params();
    g_state.params.n_ctx = 2048;  // 增加上下文大小
    g_state.params.n_batch = 512;  // 增加 batch 大小以利用 GPU
    g_state.params.n_threads = 4;
    g_state.params.n_threads_batch = 4;
    g_state.params.n_ubatch = 512;
    
    // 初始化模型参数 - 启用 GPU 加速
    struct llama_model_params model_params = llama_model_default_params();
    model_params.n_gpu_layers = 35;  // 启用 GPU 加速，将层卸载到 GPU（Vulkan/NNAPI）
    model_params.use_mmap = true;   // 使用内存映射
    model_params.use_mlock = false;
    
    LOGI("Loading model with mmap=%s, n_ctx=%d, n_batch=%d, n_threads=%d",
         model_params.use_mmap ? "true" : "false",
         g_state.params.n_ctx,
         g_state.params.n_batch,
         g_state.params.n_threads);
    
    // 加载模型
    g_state.model = llama_model_load_from_file(path, model_params);
    if (!g_state.model) {
        LOGE("Failed to load model: llama_model_load_from_file returned null");
        LOGE("Possible causes:");
        LOGE("  1. Model file is corrupted");
        LOGE("  2. Model file format is not supported (need GGUF format)");
        LOGE("  3. Not enough memory to load the model (need ~16GB for 14GB model)");
        LOGE("  4. Model quantization is not supported by this version of llama.cpp");
        LOGE("  5. Model requires more RAM than available on device");
        env->ReleaseStringUTFChars(modelPath, path);
        return 0;
    }
    
    // 创建上下文
    g_state.ctx = llama_init_from_model(g_state.model, g_state.params);
    if (!g_state.ctx) {
        LOGE("Failed to create context");
        llama_model_free(g_state.model);
        g_state.model = nullptr;
        env->ReleaseStringUTFChars(modelPath, path);
        return 0;
    }
    
    // 初始化采样器链
    struct llama_sampler_chain_params sampler_params = llama_sampler_chain_default_params();
    g_state.sampler = llama_sampler_chain_init(sampler_params);
    
    // 添加采样器
    llama_sampler_chain_add(g_state.sampler, llama_sampler_init_top_k(40));
    llama_sampler_chain_add(g_state.sampler, llama_sampler_init_top_p(0.9f, 1));
    llama_sampler_chain_add(g_state.sampler, llama_sampler_init_temp(0.7f));
    llama_sampler_chain_add(g_state.sampler, llama_sampler_init_dist(LLAMA_DEFAULT_SEED));
    
    LOGI("Model loaded successfully");
    env->ReleaseStringUTFChars(modelPath, path);
    
    return (jlong)(intptr_t)g_state.ctx;
}

// 生成文本
static jstring nativeGenerate(JNIEnv* env, jobject thiz, jlong ctxPtr, jstring prompt, jint maxTokens) {
    // 推理期间持有锁，避免与重新加载/释放并发
    std::lock_guard<std::mutex> lock(g_state_mutex);
    
    if (!g_state.ctx) {
        LOGE("Context not initialized");
        return env->NewStringUTF("");
    }
    
    const char* promptStr = env->GetStringUTFChars(prompt, nullptr);
    LOGI("Generating response for: %.50s...", promptStr);
    
    // 分词 - 使用 vocab
    const struct llama_vocab* vocab = llama_model_get_vocab(g_state.model);
    
    // 第一步：tokens 传 nullptr、n_tokens_max 传 0，查询所需 token 数量。
    // llama.cpp 的约定是：缓冲区不足时返回负值 -n_tokens，即所需长度。
    int n_tokens = llama_tokenize(vocab, promptStr, (int32_t)strlen(promptStr), nullptr, 0, true, true);
    if (n_tokens < 0) {
        n_tokens = -n_tokens;
    }
    if (n_tokens <= 0) {
        LOGE("Tokenization failed: empty prompt");
        env->ReleaseStringUTFChars(prompt, promptStr);
        return env->NewStringUTF("");
    }
    
    // 缓冲区按「实际需要」与 n_ctx 两者中的较大值分配（+64 预留），
    // 不再写死 512，否则提示超过 512 token 时 llama_tokenize 必然返回负值导致生成失败。
    const int n_ctx = g_state.params.n_ctx > 0 ? g_state.params.n_ctx : 2048;
    const int buf_size = std::max(n_tokens, n_ctx) + 64;
    std::vector<llama_token> tokens(buf_size);
    
    n_tokens = llama_tokenize(vocab, promptStr, (int32_t)strlen(promptStr), tokens.data(), (int32_t)tokens.size(), true, true);
    if (n_tokens < 0) {
        // 仍不足（理论上不会发生）：按返回的所需长度再重试一次
        LOGE("Tokenization retry: need %d tokens, buffer was %d", -n_tokens, buf_size);
        std::vector<llama_token> retry(-n_tokens);
        n_tokens = llama_tokenize(vocab, promptStr, (int32_t)strlen(promptStr), retry.data(), (int32_t)retry.size(), true, true);
        if (n_tokens < 0) {
            LOGE("Tokenization failed");
            env->ReleaseStringUTFChars(prompt, promptStr);
            return env->NewStringUTF("");
        }
        tokens.swap(retry);
    }
    
    if (n_tokens > n_ctx) {
        LOGE("Prompt has %d tokens, exceeding n_ctx=%d; llama_decode may fail", n_tokens, n_ctx);
    }
    
    // 生成
    std::string response;
    llama_batch batch = llama_batch_get_one(tokens.data(), n_tokens);
    
    if (llama_decode(g_state.ctx, batch) != 0) {
        LOGE("Failed to decode input");
        env->ReleaseStringUTFChars(prompt, promptStr);
        return env->NewStringUTF("");
    }
    
    // 采样生成
    for (int i = 0; i < maxTokens; i++) {
        // 使用采样器采样
        llama_token new_token = llama_sampler_sample(g_state.sampler, g_state.ctx, -1);
        
        // 检查是否结束
        if (llama_vocab_is_eog(vocab, new_token)) {
            break;
        }
        
        // 转换为文本
        char buf[16];
        int n = llama_token_to_piece(vocab, new_token, buf, sizeof(buf), 0, false);
        if (n > 0) {
            response.append(buf, n);
        }
        
        // 解码下一个 token
        batch = llama_batch_get_one(&new_token, 1);
        if (llama_decode(g_state.ctx, batch) != 0) {
            LOGE("Failed to decode generated token");
            break;
        }
    }
    
    LOGI("Generated: %.100s...", response.c_str());
    
    env->ReleaseStringUTFChars(prompt, promptStr);
    return env->NewStringUTF(response.c_str());
}

// 重置上下文
static void nativeReset(JNIEnv* env, jobject thiz, jlong ctxPtr) {
    std::lock_guard<std::mutex> lock(g_state_mutex);
    
    if (g_state.ctx) {
        // 清除 KV 缓存 - 使用新的 memory API
        llama_memory_t mem = llama_get_memory(g_state.ctx);
        if (mem) {
            llama_memory_clear(mem, true);
        }
        // 重置采样器状态
        if (g_state.sampler) {
            llama_sampler_reset(g_state.sampler);
        }
        LOGI("Context reset");
    }
}

// 释放资源
static void nativeFree(JNIEnv* env, jobject thiz, jlong ctxPtr) {
    std::lock_guard<std::mutex> lock(g_state_mutex);
    
    freeStateLocked();
    
    LOGI("Resources freed");
}

// 获取模型信息
static jstring nativeGetModelInfo(JNIEnv* env, jobject thiz, jlong ctxPtr) {
    std::lock_guard<std::mutex> lock(g_state_mutex);
    
    if (!g_state.model) {
        return env->NewStringUTF("Model not loaded");
    }
    
    std::string info = "Model loaded successfully\n";
    info += "Context size: " + std::to_string(g_state.params.n_ctx) + "\n";
    info += "Batch size: " + std::to_string(g_state.params.n_batch) + "\n";
    
    return env->NewStringUTF(info.c_str());
}

// 注册本地方法
static int registerLlamaEngine(JNIEnv* env) {
    jclass clazz = env->FindClass("com/example/qwenchat/LlamaEngine");
    if (!clazz) {
        LOGE("Class not found: com/example/qwenchat/LlamaEngine");
        return JNI_ERR;
    }
    
    jint result = env->RegisterNatives(clazz, methods, sizeof(methods) / sizeof(methods[0]));
    if (result != JNI_OK) {
        LOGE("Failed to register natives");
        return JNI_ERR;
    }
    
    return JNI_OK;
}

// JNI 入口
JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void* reserved) {
    JNIEnv* env = nullptr;
    
    if (vm->GetEnv((void**)&env, JNI_VERSION_1_6) != JNI_OK) {
        return JNI_ERR;
    }
    
    if (registerLlamaEngine(env) != JNI_OK) {
        return JNI_ERR;
    }
    
    return JNI_VERSION_1_6;
}