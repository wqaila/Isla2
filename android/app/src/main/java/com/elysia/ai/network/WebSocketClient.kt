package com.elysia.ai.network

import android.util.Log
import com.elysia.ai.data.ConnectionState
import com.elysia.ai.data.EmotionData
import kotlin.math.pow
import com.elysia.ai.data.WsMessage
import com.google.gson.Gson
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import okhttp3.*
import java.util.concurrent.TimeUnit

class WebSocketClient {
    private val TAG = "WebSocketClient"
    private val gson = Gson()
    
    private var webSocket: WebSocket? = null
    private var client: OkHttpClient = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    
    // 连接状态
    private val _connectionState = MutableStateFlow(ConnectionState.DISCONNECTED)
    val connectionState: StateFlow<ConnectionState> = _connectionState
    
    // 消息流
    private val _messages = MutableSharedFlow<WsMessage>(extraBufferCapacity = 64)
    val messages: SharedFlow<WsMessage> = _messages
    
    // 流式内容
    private val _streamContent = MutableSharedFlow<String>(extraBufferCapacity = 256)
    val streamContent: SharedFlow<String> = _streamContent
    
    // 完整回复
    private val _completeResponse = MutableSharedFlow<Pair<String, WsMessage>>(extraBufferCapacity = 16)
    val completeResponse: SharedFlow<Pair<String, WsMessage>> = _completeResponse
    
    // 错误消息
    private val _errorMessages = MutableSharedFlow<String>(extraBufferCapacity = 16)
    val errorMessages: SharedFlow<String> = _errorMessages
    
    // 情感分析结果
    private val _emotionData = MutableSharedFlow<EmotionData>(extraBufferCapacity = 16)
    val emotionData: SharedFlow<EmotionData> = _emotionData
    
    private var sessionId: String? = null
    private var serverUrl: String = ""
    private var deviceId: String = "android"
    private var deviceName: String = ""
    private var usePublicUrl: Boolean = false
    private var publicUrl: String = ""
    
    // 心跳任务
    private var heartbeatJob: Job? = null
    
    // 自动重连
    private var reconnectJob: Job? = null
    private var reconnectAttempts = 0
    private val maxReconnectAttempts = 5
    private var shouldReconnect = true
    
    fun connect(serverUrl: String, deviceId: String = "android", deviceName: String = "",
                usePublicUrl: Boolean = false, publicUrl: String = "") {
        this.serverUrl = serverUrl
        this.deviceId = deviceId
        this.deviceName = deviceName
        this.usePublicUrl = usePublicUrl
        this.publicUrl = publicUrl
        this.shouldReconnect = true
        this.reconnectAttempts = 0
        
        _connectionState.value = ConnectionState.CONNECTING
        
        // 根据连接类型选择协议（公网使用 WSS，局域网使用 WS）
        val wsUrl = if (usePublicUrl && publicUrl.isNotBlank()) {
            // 公网地址：使用 wss:// 协议
            val host = publicUrl.removePrefix("https://").removePrefix("http://").removeSuffix("/")
            "wss://$host/ws/chat?device_id=$deviceId&device_name=$deviceName"
        } else {
            // 局域网地址：使用 ws:// 协议
            "ws://$serverUrl/ws/chat?device_id=$deviceId&device_name=$deviceName"
        }
        Log.d(TAG, "Connecting to: $wsUrl")
        
        val request = Request.Builder()
            .url(wsUrl)
            .build()
        
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.d(TAG, "WebSocket connected")
                _connectionState.value = ConnectionState.CONNECTED
                startHeartbeat()
            }
            
            override fun onMessage(webSocket: WebSocket, text: String) {
                Log.d(TAG, "Received: $text")
                try {
                    val wsMsg = gson.fromJson(text, WsMessage::class.java)
                    
                    // 处理连接确认
                    if (wsMsg.type == "connected") {
                        sessionId = wsMsg.sessionId
                        Log.d(TAG, "Session ID: $sessionId")
                    }
                    
                    // 处理情感分析结果
                    if (wsMsg.type == "emotion") {
                        val dominant = wsMsg.dominant ?: "neutral"
                        val emoji = wsMsg.emoji ?: "😶"
                        val intensity = wsMsg.intensity
                        val valence = wsMsg.valence
                        val summary = wsMsg.summary ?: ""
                        val emotion = EmotionData(
                            dominant = dominant,
                            emoji = emoji,
                            intensity = intensity,
                            valence = valence,
                            summary = summary
                        )
                        scope.launch {
                            _emotionData.emit(emotion)
                        }
                    }
                    
                    // 发送到消息流
                    scope.launch {
                        _messages.emit(wsMsg)
                    }
                    
                    // 处理流式内容
                    if (wsMsg.type == "stream") {
                        if (!wsMsg.done && wsMsg.content != null) {
                            scope.launch {
                                _streamContent.emit(wsMsg.content)
                            }
                        } else if (wsMsg.done) {
                            scope.launch {
                                _completeResponse.emit(Pair(wsMsg.type, wsMsg))
                            }
                        }
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "Error parsing message", e)
                }
            }
            
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                Log.d(TAG, "WebSocket closing: $code $reason")
                webSocket.close(1000, null)
                handleDisconnect()
            }
            
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "WebSocket failure", t)
                _connectionState.value = ConnectionState.ERROR
                handleDisconnect()
                scope.launch {
                    _errorMessages.emit("连接失败: ${t.message}")
                }
                scheduleReconnect()
            }
        })
    }
    
    fun disconnect() {
        shouldReconnect = false  // 主动断开时禁止自动重连
        heartbeatJob?.cancel()
        reconnectJob?.cancel()
        webSocket?.close(1000, "User disconnect")
        webSocket = null
        _connectionState.value = ConnectionState.DISCONNECTED
    }
    
    private fun handleDisconnect() {
        heartbeatJob?.cancel()
        _connectionState.value = ConnectionState.DISCONNECTED
    }
    
    private fun scheduleReconnect() {
        if (!shouldReconnect || reconnectAttempts >= maxReconnectAttempts) {
            if (reconnectAttempts >= maxReconnectAttempts) {
                scope.launch {
                    _errorMessages.emit("重连失败，请检查网络后手动重连")
                }
            }
            return
        }
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            val delayMs = (2.0.pow(reconnectAttempts.toDouble()) * 1000).toLong() // 指数退避: 1s, 2s, 4s, 8s, 16s
            Log.d(TAG, "Reconnecting in ${delayMs}ms (attempt ${reconnectAttempts + 1}/$maxReconnectAttempts)")
            _connectionState.value = ConnectionState.CONNECTING
            delay(delayMs)
            reconnectAttempts++
            if (serverUrl.isNotEmpty()) {
                connect(serverUrl, deviceId, deviceName, usePublicUrl, publicUrl)
            }
        }
    }
    
    private fun resetReconnect() {
        reconnectAttempts = 0
        reconnectJob?.cancel()
    }
    
    private fun startHeartbeat() {
        heartbeatJob?.cancel()
        heartbeatJob = scope.launch {
            while (isActive) {
                delay(30_000) // 30秒心跳
                try {
                    webSocket?.send("""{"type":"heartbeat"}""")
                } catch (e: Exception) {
                    Log.e(TAG, "Heartbeat failed", e)
                    break
                }
            }
        }
    }
    
    fun sendMessage(message: String) {
        val wsMsg = WsMessage(
            type = "chat",
            message = message
        )
        webSocket?.send(gson.toJson(wsMsg))
    }
    
    fun getSessionId(): String? = sessionId
    
    fun isConnected(): Boolean = _connectionState.value == ConnectionState.CONNECTED
    
    fun reconnect() {
        disconnect()
        shouldReconnect = true
        reconnectAttempts = 0
        if (serverUrl.isNotEmpty()) {
            connect(serverUrl, deviceId, deviceName, usePublicUrl, publicUrl)
        }
    }
    
    fun destroy() {
        disconnect()
        scope.cancel()
    }
}