package com.elysia.ai.viewmodel

import android.app.Application
import android.os.Build
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.elysia.ai.data.*
import com.elysia.ai.network.WebSocketClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID

class ChatViewModel(application: Application) : AndroidViewModel(application) {
    
    companion object {
        private const val TAG = "ChatViewModel"
    }
    
    private val wsClient = WebSocketClient()
    private val prefs = PreferencesManager(application.applicationContext)
    
    // ===== UI 状态 =====
    private val _messages = MutableStateFlow<List<ChatMessage>>(emptyList())
    val messages: StateFlow<List<ChatMessage>> = _messages
    
    private val _connectionState = MutableStateFlow(ConnectionState.DISCONNECTED)
    val connectionState: StateFlow<ConnectionState> = _connectionState
    
    private val _serverConfig = MutableStateFlow(ServerConfig())
    val serverConfig: StateFlow<ServerConfig> = _serverConfig
    
    private val _isStreaming = MutableStateFlow(false)
    val isStreaming: StateFlow<Boolean> = _isStreaming
    
    private val _errorMessage = MutableSharedFlow<String>()
    val errorMessage: SharedFlow<String> = _errorMessage
    
    private val _currentStreamingMessage = MutableStateFlow("")
    val currentStreamingMessage: StateFlow<String> = _currentStreamingMessage
    
    private val _stats = MutableStateFlow(ServerStats())
    val stats: StateFlow<ServerStats> = _stats
    
    // 情感状态
    private val _emotion = MutableStateFlow<EmotionData?>(null)
    val emotion: StateFlow<EmotionData?> = _emotion
    
    // 主题状态
    private val _isDarkMode = MutableStateFlow(true)
    val isDarkMode: StateFlow<Boolean> = _isDarkMode
    
    // 代次标记：用于防止重新生成时旧流的 completeResponse 错误触发 finishStreaming
    private var streamGeneration = 0
    
    // 是否已自动连接
    private var hasAutoConnected = false
    
    init {
        // 同步加载配置（轻量操作，安全）
        val savedConfig = prefs.loadServerConfig()
        _serverConfig.value = savedConfig
        _isDarkMode.value = prefs.loadDarkMode()
        
        // 异步加载历史消息（避免 Gson 反序列化阻塞主线程导致 ANR/闪退）
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val savedMessages = prefs.loadMessages()
                withContext(Dispatchers.Main) {
                    if (savedMessages.isNotEmpty()) {
                        _messages.value = savedMessages
                    } else {
                        addWelcomeMessage()
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "加载历史消息失败", e)
                withContext(Dispatchers.Main) {
                    addWelcomeMessage()
                }
            }
        }
        
        // 监听连接状态
        viewModelScope.launch {
            wsClient.connectionState.collect { state ->
                _connectionState.value = state
                // 连接成功后保存会话 ID
                if (state == ConnectionState.CONNECTED) {
                    wsClient.getSessionId()?.let { prefs.saveSessionId(it) }
                }
            }
        }
        
        // 监听流式内容
        viewModelScope.launch {
            wsClient.streamContent.collect { chunk ->
                _currentStreamingMessage.value += chunk
            }
        }
        
        // 监听完整回复（仅处理当前代次的响应）
        viewModelScope.launch {
            wsClient.completeResponse.collect { (_, wsMsg) ->
                finishStreaming(wsMsg.tokens, wsMsg.responseMs)
            }
        }
        
        // 监听错误消息
        viewModelScope.launch {
            wsClient.errorMessages.collect { error ->
                _errorMessage.emit(error)
            }
        }
        
        // 监听情感分析结果
        viewModelScope.launch {
            wsClient.emotionData.collect { emotion ->
                _emotion.value = emotion
            }
        }
        
        // 延迟自动连接（避免 init 中网络操作阻塞主线程）
        if (savedConfig.autoConnect && !hasAutoConnected) {
            hasAutoConnected = true
            viewModelScope.launch {
                delay(500) // 等待 UI 渲染完成
                connect()
            }
        }
    }
    
    private fun addWelcomeMessage() {
        val welcomeMsg = ChatMessage(
            id = UUID.randomUUID().toString(),
            role = "assistant",
            content = "你好呀~我是爱莉希雅，很高兴见到你呢！✨\n有什么想聊的吗？",
            timestamp = System.currentTimeMillis()
        )
        _messages.value = listOf(welcomeMsg)
    }
    
    fun updateConfig(config: ServerConfig) {
        _serverConfig.value = config
        // 持久化配置
        prefs.saveServerConfig(config)
    }
    
    fun connect() {
        val config = _serverConfig.value
        val url = "${config.serverIp}:${config.serverPort}"
        val deviceName = Build.MODEL ?: "Android"
        wsClient.connect(
            serverUrl = url,
            deviceId = "android_${Build.DEVICE}",
            deviceName = deviceName,
            usePublicUrl = config.usePublicUrl,
            publicUrl = config.publicUrl
        )
    }
    
    fun disconnect() {
        wsClient.disconnect()
    }
    
    fun reconnect() {
        wsClient.reconnect()
    }
    
    fun sendMessage(content: String) {
        if (content.isBlank()) return
        if (!wsClient.isConnected()) {
            viewModelScope.launch {
                _errorMessage.emit("请先连接到服务器")
            }
            return
        }
        
        // 增加代次标记
        streamGeneration++
        
        // 添加用户消息（BUG #4 FIX: 初始状态为 SENDING）
        val userMsg = ChatMessage(
            id = UUID.randomUUID().toString(),
            role = "user",
            content = content.trim(),
            timestamp = System.currentTimeMillis(),
            status = MessageStatus.SENDING
        )
        _messages.value = _messages.value + userMsg
        
        // 发送后标记为 SENT
        val sentMessages = _messages.value.toMutableList()
        val lastIdx = sentMessages.lastIndex
        sentMessages[lastIdx] = sentMessages[lastIdx].copy(status = MessageStatus.SENT)
        _messages.value = sentMessages
        
        // 开始流式接收
        _isStreaming.value = true
        _currentStreamingMessage.value = ""
        
        // 添加 AI 占位消息
        val aiMsg = ChatMessage(
            id = UUID.randomUUID().toString(),
            role = "assistant",
            content = "",
            timestamp = System.currentTimeMillis(),
            isStreaming = true
        )
        _messages.value = _messages.value + aiMsg
        
        // 发送到服务器
        wsClient.sendMessage(content.trim())
        
        // 持久化消息
        persistMessages()
    }
    
    private fun finishStreaming(tokens: Int, responseMs: Int) {
        val streamingContent = _currentStreamingMessage.value
        val currentMessages = _messages.value.toMutableList()
        val lastIndex = currentMessages.lastIndex
        if (lastIndex >= 0 && currentMessages[lastIndex].role == "assistant") {
            // BUG #5 FIX: 空回复也更新状态，避免永久闪烁光标
            currentMessages[lastIndex] = currentMessages[lastIndex].copy(
                content = streamingContent.ifEmpty { "（无回复内容）" },
                tokens = tokens,
                responseMs = responseMs,
                isStreaming = false,
                status = MessageStatus.DELIVERED
            )
            _messages.value = currentMessages
        }
        
        _isStreaming.value = false
        _currentStreamingMessage.value = ""
        
        // 持久化消息
        persistMessages()
    }
    
    fun clearMessages() {
        _messages.value = emptyList()
        addWelcomeMessage()
        prefs.clearMessages()
    }
    
    /** 复制消息内容到剪贴板（返回内容供 UI 使用） */
    fun getMessageContent(messageId: String): String? {
        return _messages.value.find { it.id == messageId }?.content
    }
    
    /** 重新发送用户消息 */
    fun resendMessage(messageId: String) {
        val msg = _messages.value.find { it.id == messageId && it.role == "user" }
        if (msg != null) {
            // 移除该消息之后的所有消息（包括失败的 AI 回复）
            val idx = _messages.value.indexOf(msg)
            _messages.value = _messages.value.take(idx)
            // 重新发送
            sendMessage(msg.content)
        }
    }
    
    /** 重新生成 AI 回复 */
    fun regenerateResponse(messageId: String) {
        val msg = _messages.value.find { it.id == messageId && it.role == "assistant" }
        if (msg != null) {
            val idx = _messages.value.indexOf(msg)
            // 找到这条 AI 消息之前的用户消息
            val userMsg = _messages.value.take(idx).lastOrNull { it.role == "user" }
            // 移除该 AI 消息
            _messages.value = _messages.value.toMutableList().also { it.removeAt(idx) }
            if (userMsg != null) {
                // 移除该用户消息，重新发送
                val userIdx = _messages.value.indexOf(userMsg)
                _messages.value = _messages.value.take(userIdx)
                sendMessage(userMsg.content)
            }
        }
    }
    
    // ===== 主题切换 =====
    
    fun toggleDarkMode() {
        val newValue = !_isDarkMode.value
        _isDarkMode.value = newValue
        prefs.saveDarkMode(newValue)
    }
    
    // ===== 持久化辅助 =====
    
    private fun persistMessages() {
        // 过滤掉空的 streaming 占位消息
        val toSave = _messages.value.filter { !(it.role == "assistant" && it.content.isEmpty()) }
        prefs.saveMessages(toSave)
    }
    
    override fun onCleared() {
        super.onCleared()
        persistMessages()
        wsClient.destroy()
    }
}