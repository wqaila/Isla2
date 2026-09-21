package com.example.qwenchat

import android.content.Context
import android.util.Log
import java.io.File

/**
 * llama.cpp 引擎封装
 * 通过 JNI 调用本地 llama.cpp 库
 */
class LlamaEngine(private val context: Context) {

    companion object {
        private const val TAG = "LlamaEngine"
        private var nativeLibsLoaded = false
        
        // 加载 native 库
        init {
            try {
                Log.d(TAG, "Starting native library loading...")
                // 只加载 llama-helper 库，llama 库已静态链接到其中
                System.loadLibrary("llama-helper")
                Log.d(TAG, "llama-helper library loaded successfully")
                nativeLibsLoaded = true
                Log.d(TAG, "All native libraries loaded successfully")
            } catch (e: UnsatisfiedLinkError) {
                Log.e(TAG, "Failed to load native library: ${e.message}")
                e.printStackTrace()
            } catch (e: Exception) {
                Log.e(TAG, "Unexpected error loading native library: ${e.message}")
                e.printStackTrace()
            }
        }
    }

    private var nativeContext: Long = 0
    private var isInitialized = false

    /**
     * 加载模型
     * @param modelPath GGUF 模型文件的完整路径
     * @return 是否加载成功
     */
    fun loadModel(modelPath: String): Boolean {
        // 检查 native 库是否已加载
        if (!nativeLibsLoaded) {
            Log.e(TAG, "Native libraries not loaded, cannot load model")
            return false
        }
        
        return try {
            Log.d(TAG, "Loading model from: $modelPath")
            
            val modelFile = File(modelPath)
            if (!modelFile.exists()) {
                Log.e(TAG, "Model file not found: $modelPath")
                return false
            }
            
            // 调用 native 方法加载模型
            nativeContext = nativeLoadModel(modelPath)
            isInitialized = nativeContext != 0L
            
            Log.d(TAG, "Model loaded: $isInitialized")
            isInitialized
        } catch (e: UnsatisfiedLinkError) {
            Log.e(TAG, "JNI method not found: ${e.message}")
            e.printStackTrace()
            false
        } catch (e: Exception) {
            Log.e(TAG, "Error loading model: ${e.message}")
            e.printStackTrace()
            false
        }
    }

    /**
     * 生成文本回复
     * @param prompt 输入提示
     * @param maxTokens 最大生成长度
     * @return 生成的文本
     */
    fun generate(prompt: String, maxTokens: Int = 512): String {
        if (!isInitialized || nativeContext == 0L) {
            Log.e(TAG, "Model not loaded")
            return ""
        }

        return try {
            Log.d(TAG, "Generating response for: $prompt")
            
            val response = nativeGenerate(nativeContext, prompt, maxTokens)
            Log.d(TAG, "Generated: ${response.take(50)}...")
            
            response
        } catch (e: Exception) {
            Log.e(TAG, "Error generating: ${e.message}")
            ""
        }
    }

    /**
     * 流式生成（通过回调返回部分结果）
     * 注意：当前版本使用完整生成，模拟流式效果
     * @param prompt 输入提示
     * @param maxTokens 最大生成长度
     * @param callback 回调函数，接收生成的文本片段
     */
    fun generateStream(
        prompt: String, 
        maxTokens: Int = 512,
        callback: (String) -> Unit
    ) {
        if (!isInitialized || nativeContext == 0L) {
            Log.e(TAG, "Model not loaded")
            callback("")
            return
        }

        try {
            // 使用完整生成，然后分块回调
            val fullResponse = nativeGenerate(nativeContext, prompt, maxTokens)
            // 按字符分块模拟流式效果
            var chunk = StringBuilder()
            for (char in fullResponse) {
                chunk.append(char)
                if (chunk.length >= 10) {
                    callback(chunk.toString())
                    chunk.clear()
                }
            }
            if (chunk.isNotEmpty()) {
                callback(chunk.toString())
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error in stream generation: ${e.message}")
            callback("")
        }
    }

    /**
     * 重置生成状态
     */
    fun reset() {
        if (nativeContext != 0L) {
            nativeReset(nativeContext)
        }
    }

    /**
     * 释放资源
     */
    fun release() {
        if (nativeContext != 0L) {
            nativeFree(nativeContext)
            nativeContext = 0L
            isInitialized = false
            Log.d(TAG, "Resources released")
        }
    }

    /**
     * 检查模型是否已加载
     */
    fun isModelLoaded(): Boolean = isInitialized

    /**
     * 获取模型信息
     */
    fun getModelInfo(): String {
        if (!isInitialized || nativeContext == 0L) {
            return "Model not loaded"
        }
        return nativeGetModelInfo(nativeContext)
    }


    // Native 方法声明
    external fun nativeLoadModel(modelPath: String): Long
    external fun nativeGenerate(ctx: Long, prompt: String, maxTokens: Int): String
    external fun nativeReset(ctx: Long)
    external fun nativeFree(ctx: Long)
    external fun nativeGetModelInfo(ctx: Long): String
}
