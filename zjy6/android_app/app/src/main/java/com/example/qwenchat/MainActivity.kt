package com.example.qwenchat

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import android.text.Editable
import android.text.TextWatcher
import android.util.Log
import android.view.View
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.example.qwenchat.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

data class ChatMessage(
    var role: String,
    var content: String,
    val isUser: Boolean
)

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private var llamaEngine: LlamaEngine? = null
    private var isModelLoaded = false
    private var isModelLoading = false
    private val chatMessages = mutableListOf<ChatMessage>()
    private var isGenerating = false
    
    private val maxHistoryMessages = 10
    private var modelType = "qwen"
    
    companion object {
        private const val TAG = "MainActivity"
        private const val STORAGE_PERMISSION_REQUEST_CODE = 1001
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        try {
            binding = ActivityMainBinding.inflate(layoutInflater)
            setContentView(binding.root)
            setupUI()
            checkStoragePermissionAndLoad()
        } catch (e: Exception) {
            e.printStackTrace()
            val textView = TextView(this)
            textView.text = "应用初始化失败：${e.message}"
            textView.setPadding(16, 16, 16, 16)
            setContentView(textView)
        }
    }
    
    private fun setupUI() {
        binding.sendButton.setOnClickListener { sendMessage() }
        binding.clearButton.setOnClickListener { clearChatHistory() }
        
        binding.inputEditText.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                val hasText = !s.isNullOrBlank()
                binding.sendButton.isEnabled = hasText && isModelLoaded && !isGenerating
            }
        })
    }

    /**
     * 检查存储权限，授权后再加载模型。
     * 权限申请必须在主线程发起，因此这里由 onCreate / onResume 调用，不再放进 IO 协程。
     */
    private fun checkStoragePermissionAndLoad() {
        when {
            // Android 11+（API 30+）：读取 /sdcard 下的任意文件（如 .gguf）需要「所有文件访问权限」。
            // 注意 READ_EXTERNAL_STORAGE 在 API 33+ 已失效，READ_MEDIA_* 只覆盖媒体文件、读不到 .gguf。
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.R -> {
                if (Environment.isExternalStorageManager()) {
                    loadModel()
                } else {
                    requestAllFilesAccess()
                }
            }
            // Android 6.0 ~ 10：运行时申请读取外部存储权限
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.M -> {
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.READ_EXTERNAL_STORAGE)
                    == PackageManager.PERMISSION_GRANTED) {
                    loadModel()
                } else {
                    ActivityCompat.requestPermissions(
                        this,
                        arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE),
                        STORAGE_PERMISSION_REQUEST_CODE
                    )
                }
            }
            // Android 6.0 以下安装即授权
            else -> loadModel()
        }
    }

    /**
     * 引导用户到系统设置授予「所有文件访问权限」（MANAGE_EXTERNAL_STORAGE）
     */
    private fun requestAllFilesAccess() {
        Toast.makeText(this, "需要「所有文件访问权限」才能读取外部存储中的模型文件", Toast.LENGTH_LONG).show()
        try {
            startActivity(
                Intent(
                    Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                    Uri.parse("package:$packageName")
                )
            )
        } catch (e: Exception) {
            Log.w(TAG, "无法打开应用专属权限设置页，改用通用设置页：${e.message}")
            try {
                startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
            } catch (e2: Exception) {
                Log.e(TAG, "无法打开所有文件访问权限设置页：${e2.message}")
                Toast.makeText(this, "请在系统设置中手动开启「所有文件访问权限」", Toast.LENGTH_LONG).show()
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // 用户从系统设置授权返回后，自动开始加载模型
        if (!isModelLoaded && !isModelLoading &&
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.R &&
            Environment.isExternalStorageManager()) {
            loadModel()
        }
    }

    private fun loadModel() {
        if (isModelLoading) return
        isModelLoading = true
        binding.statusTextView.text = "正在加载模型..."
        binding.sendButton.isEnabled = false
        
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                llamaEngine = LlamaEngine(this@MainActivity)
                
                // 依次在外部存储根目录和应用私有目录中查找模型。
                // 应用私有目录无需任何存储权限，权限被拒时仍可能正常使用。
                val searchDirs = mutableListOf(Environment.getExternalStorageDirectory())
                getExternalFilesDir(null)?.let { searchDirs.add(it) }
                
                val modelFiles = searchDirs.flatMap { findModelFiles(it) }
                Log.d(TAG, "找到 ${modelFiles.size} 个模型文件")
                
                var success = false
                var loadedModel = ""
                
                for (modelFile in modelFiles) {
                    try {
                        Log.d(TAG, "尝试加载模型：${modelFile.absolutePath}")
                        detectModelType(modelFile.name)
                        
                        if (llamaEngine?.loadModel(modelFile.absolutePath) == true) {
                            success = true
                            loadedModel = modelFile.name
                            break
                        }
                    } catch (e: Exception) {
                        Log.w(TAG, "加载 ${modelFile.name} 失败：${e.message}")
                    }
                }
                
                withContext(Dispatchers.Main) {
                    isModelLoading = false
                    if (success) {
                        isModelLoaded = true
                        val modelTypeName = if (modelType == "gemma") "Gemma" else "Qwen"
                        binding.statusTextView.text = "模型加载完成 ($loadedModel)"
                        binding.sendButton.isEnabled = true
                        Toast.makeText(this@MainActivity, "模型加载成功：$loadedModel ($modelTypeName)", Toast.LENGTH_LONG).show()
                        
                        val welcomeMessage = if (modelType == "gemma") {
                            "Hello! I am Gemma, running locally on your device."
                        } else {
                            "你好！我是 Qwen-7B 助手，运行在你的设备上。"
                        }
                        appendMessage(ChatMessage(role = "Assistant", content = welcomeMessage, isUser = false))
                    } else {
                        binding.statusTextView.text = "模型加载失败"
                        Toast.makeText(
                            this@MainActivity,
                            "未找到可用的 GGUF 模型，请把模型文件放到 /sdcard/ 或应用私有目录",
                            Toast.LENGTH_LONG
                        ).show()
                    }
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    isModelLoading = false
                    binding.statusTextView.text = "加载出错：${e.message}"
                }
            }
        }
    }
    
    private fun findModelFiles(dir: File, maxDepth: Int = 3): List<File> {
        val modelFiles = mutableListOf<File>()
        
        fun searchDirectory(currentDir: File, depth: Int) {
            if (depth > maxDepth) return
            try {
                val files = currentDir.listFiles() ?: return
                for (file in files) {
                    try {
                        if (file.isDirectory) {
                            searchDirectory(file, depth + 1)
                        } else if (file.name.endsWith(".gguf", ignoreCase = true)) {
                            modelFiles.add(file)
                        }
                    } catch (e: Exception) {
                        Log.w(TAG, "读取文件信息失败：${file.name}：${e.message}")
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "遍历目录失败：${currentDir.absolutePath}：${e.message}")
            }
        }
        
        searchDirectory(dir, 0)
        // 优先加载体积最大的模型（通常是量化等级更高、更完整的版本），而不是文件名最短的
        return modelFiles.sortedByDescending { it.length() }
    }
    
    private fun sendMessage() {
        val message = binding.inputEditText.text.toString().trim()
        if (message.isEmpty()) return
        if (!isModelLoaded) {
            Toast.makeText(this, "模型正在加载中...", Toast.LENGTH_SHORT).show()
            return
        }
        if (isGenerating) {
            Toast.makeText(this, "正在生成回复", Toast.LENGTH_SHORT).show()
            return
        }

        appendMessage(ChatMessage(role = "User", content = message, isUser = true))
        binding.inputEditText.text?.clear()
        val prompt = buildPromptWithHistory()
        generateResponse(prompt)
    }
    
    private fun detectModelType(filename: String) {
        val lowerName = filename.lowercase()
        when {
            lowerName.contains("gemma") -> modelType = "gemma"
            lowerName.contains("qwen") -> modelType = "qwen"
            else -> modelType = "qwen"
        }
        Log.d(TAG, "模型类型：$modelType")
    }
    
    private fun buildPromptWithHistory(): String {
        return when (modelType) {
            "gemma" -> buildGemmaPrompt()
            else -> buildQwenPrompt()
        }
    }
    
    /**
     * 只读取 chatMessages（用户消息已在 sendMessage 中追加过一次），
     * 末尾补上 assistant 起始标记即可，不再单独拼接 userMessage，避免用户输入重复。
     */
    private fun buildQwenPrompt(): String {
        val sb = StringBuilder()
        sb.append("<|im_start|>system\n你是一位有用的人工智能助手。<|im_end|>\n")
        for (msg in chatMessages.takeLast(maxHistoryMessages)) {
            val role = if (msg.isUser) "user" else "assistant"
            sb.append("<|im_start|>$role\n${msg.content}<|im_end|>\n")
        }
        sb.append("<|im_start|>assistant\n")
        return sb.toString()
    }
    
    private fun buildGemmaPrompt(): String {
        val sb = StringBuilder()
        sb.append("<bos>")
        for (msg in chatMessages.takeLast(maxHistoryMessages)) {
            val role = if (msg.isUser) "user" else "model"
            sb.append("<start_of_turn>$role\n${msg.content}<end_of_turn>\n")
        }
        sb.append("<start_of_turn>model\n")
        return sb.toString()
    }
    
    private fun generateResponse(prompt: String) {
        isGenerating = true
        binding.statusTextView.text = "正在生成..."
        binding.sendButton.isEnabled = false
        
        // 先插入一条空的 assistant 占位消息，流式回调只更新这一条，
        // 结束时就地更新，不再追加新消息，避免消息重复。
        val assistantMessage = ChatMessage(role = "Assistant", content = "", isUser = false)
        chatMessages.add(assistantMessage)
        renderMessages()
        
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val responseBuilder = StringBuilder()
                
                llamaEngine?.generateStream(prompt, maxTokens = 512) { chunk ->
                    if (chunk.isEmpty()) return@generateStream
                    responseBuilder.append(chunk)
                    val snapshot = responseBuilder.toString()
                    // 回调在 IO 线程触发，切回主线程刷新占位消息，不能用 runBlocking 阻塞生成线程
                    lifecycleScope.launch(Dispatchers.Main) {
                        updateAssistantMessage(assistantMessage, snapshot)
                    }
                }
                
                val finalResponse = responseBuilder.toString()
                
                withContext(Dispatchers.Main) {
                    isGenerating = false
                    binding.statusTextView.text = "就绪"
                    binding.sendButton.isEnabled = true
                    
                    if (finalResponse.isNotEmpty()) {
                        updateAssistantMessage(assistantMessage, finalResponse)
                    } else {
                        updateAssistantMessage(assistantMessage, "生成失败，请重试")
                    }
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    isGenerating = false
                    binding.statusTextView.text = "就绪"
                    binding.sendButton.isEnabled = true
                    updateAssistantMessage(assistantMessage, "出错：${e.message}")
                }
            }
        }
    }
    
    private fun appendMessage(message: ChatMessage) {
        chatMessages.add(message)
        renderMessages()
    }
    
    /**
     * 就地更新指定的助手消息（占位消息），
     * 不再依赖「最后一条不是用户消息」这种脆弱条件。
     */
    private fun updateAssistantMessage(message: ChatMessage, content: String) {
        message.content = content
        renderMessages()
    }
    
    private fun renderMessages() {
        val output = chatMessages.joinToString("\n") { "${it.role}: ${it.content}" }
        binding.chatOutput.text = output
        binding.chatScrollView.post { binding.chatScrollView.fullScroll(View.FOCUS_DOWN) }
    }
    
    private fun clearChatHistory() {
        chatMessages.clear()
        // 推理进行中时 nativeReset 会等待互斥锁，直接调用会卡住主线程，这里跳过
        if (!isGenerating) {
            llamaEngine?.reset()
        }
        renderMessages()
        Toast.makeText(this, "聊天历史已清空", Toast.LENGTH_SHORT).show()
    }

    override fun onDestroy() {
        super.onDestroy()
        // native 侧有互斥锁，release() 可能等待正在进行的加载/推理结束，
        // 放到后台线程执行，避免阻塞主线程造成 ANR。
        val engine = llamaEngine
        llamaEngine = null
        if (engine != null) {
            Thread {
                try {
                    engine.release()
                } catch (e: Exception) {
                    Log.w(TAG, "释放 native 资源失败：${e.message}")
                }
            }.start()
        }
    }
    
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == STORAGE_PERMISSION_REQUEST_CODE) {
            if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                Toast.makeText(this, "存储权限已授予", Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(this, "未授予存储权限，将只从应用私有目录查找模型", Toast.LENGTH_LONG).show()
            }
            // 无论是否授权都继续加载：应用私有目录无需任何权限
            loadModel()
        }
    }
}