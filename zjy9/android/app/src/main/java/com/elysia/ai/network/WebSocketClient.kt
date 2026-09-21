package com.elysia.ai.network

import android.util.Log
import com.elysia.ai.data.ConnectionState
import com.elysia.ai.data.EmotionData
import kotlin.math.pow
import com.elysia.ai.data.WsMessage
import com.google.gson.Gson
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
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
    
    // 连接代次：每发起一次连接就自增。已被替换的旧连接（socket）回调因代次不匹配被忽略，
    // 避免旧 socket 的 onFailure/onClosing 把新连接的状态机改乱。
    @Volatile
    private var connectionSeq = 0
    
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
    
    // 收包队列：onMessage 本身是逐条串行到达的，但若每条都 launch 到 IO 线程池，
    // 分片（stream）与结束（done）事件的消费顺序就无法保证——最后几个分片可能
    // 在 done 之后才被处理，表现为丢字或串到下一轮回复里。
    // 改为单消费者串行分发，严格按到达顺序处理。
    private val incoming = Channel<WsMessage>(Channel.UNLIMITED)
    
    init {
        scope.launch {
            for (msg in incoming) {
                dispatch(msg)
            }
        }
    }
    
    /** 按到达顺序串行分发（只在单消费者协程里调用，保证顺序） */
    private suspend fun dispatch(wsMsg: WsMessage) {
        // 处理连接确认
        if (wsMsg.type == "connected") {
            sessionId = wsMsg.sessionId
            Log.d(TAG, "Session ID: $sessionId")
        }
        
        // 服务端主动返回的错误（例如"消息过长"）：交给 UI 提示，不要静默丢弃
        if (wsMsg.type == "error") {
            _errorMessages.emit(wsMsg.message ?: "服务器返回错误")
        }
        
        // 处理情感分析结果
        if (wsMsg.type == "emotion") {
            val emotion = EmotionData(
                dominant = wsMsg.dominant ?: "neutral",
                emoji = wsMsg.emoji ?: "😶",
                intensity = wsMsg.intensity,
                valence = wsMsg.valence,
                summary = wsMsg.summary ?: ""
            )
            _emotionData.emit(emotion)
        }
        
        // 发送到消息流
        _messages.emit(wsMsg)
        
        // 处理流式内容
        if (wsMsg.type == "stream") {
            // done 分片也可能带内容（超时/错误/手动终止提示），不能丢
            if (!wsMsg.content.isNullOrEmpty()) {
                _streamContent.emit(wsMsg.content)
            }
            if (wsMsg.done) {
                _completeResponse.emit(Pair(wsMsg.type, wsMsg))
            }
        }
    }
    
    /** 外部主动发起连接：重置重连计数后建立连接 */
    fun connect(serverUrl: String, deviceId: String = "android", deviceName: String = "",
                usePublicUrl: Boolean = false, publicUrl: String = "") {
        reconnectAttempts = 0
        connectInternal(serverUrl, deviceId, deviceName, usePublicUrl, publicUrl)
    }
    
    private fun connectInternal(serverUrl: String, deviceId: String, deviceName: String,
                               usePublicUrl: Boolean, publicUrl: String) {
        this.serverUrl = serverUrl
        this.deviceId = deviceId
        this.deviceName = deviceName
        this.usePublicUrl = usePublicUrl
        this.publicUrl = publicUrl
        this.shouldReconnect = true
        
        // 本次连接代次：先自增，再关旧连接，这样旧连接触发的回调会被判定为过期
        val seq = ++connectionSeq
        
        // 关闭仍然存活的旧连接，避免连接泄漏 / 同时存在两条连接
        heartbeatJob?.cancel()
        webSocket?.cancel()
        webSocket = null
        
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
            /** 该 socket 是否已被更新的连接替换 */
            private fun stale(): Boolean = seq != connectionSeq
            
            override fun onOpen(webSocket: WebSocket, response: Response) {
                if (stale()) {
                    webSocket.cancel()
                    return
                }
                Log.d(TAG, "WebSocket connected")
                resetReconnect()
                _connectionState.value = ConnectionState.CONNECTED
                startHeartbeat()
            }
            
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (stale()) return
                Log.d(TAG, "Received: $text")
                try {
                    // 只做入队（同一条连接上 onMessage 是串行的），由单消费者按序分发
                    incoming.trySend(gson.fromJson(text, WsMessage::class.java))
                } catch (e: Exception) {
                    Log.e(TAG, "Error parsing message", e)
                }
            }
            
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                if (stale()) return
                Log.d(TAG, "WebSocket closing: $code $reason")
                webSocket.close(1000, null)
                handleDisconnect()
                // 服务端主动关闭（服务重启 / 连接数已满）也要走自动重连，
                // 否则客户端会永久停在"未连接"，只能手动重连
                scheduleReconnect()
            }
            
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (stale()) return
                Log.e(TAG, "WebSocket failure", t)
                // 先收尾（取消心跳），再置为 ERROR，避免 ERROR 被 handleDisconnect 立刻覆盖掉
                handleDisconnect()
                _connectionState.value = ConnectionState.ERROR
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
        if (!shouldReconnect) return
        if (reconnectAttempts >= maxReconnectAttempts) {
            // 重试次数用尽：回到"未连接"，让 UI 显示离线遮罩并提供手动重连入口
            _connectionState.value = ConnectionState.DISCONNECTED
            scope.launch {
                _errorMessages.emit("重连失败，请检查网络后手动重连")
            }
            return
        }
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            val attempt = reconnectAttempts + 1
            val delayMs = (2.0.pow(reconnectAttempts.toDouble()) * 1000).toLong() // 指数退避: 1s, 2s, 4s, 8s, 16s
            Log.d(TAG, "Reconnecting in ${delayMs}ms (attempt $attempt/$maxReconnectAttempts)")
            _connectionState.value = ConnectionState.CONNECTING
            delay(delayMs)
            if (!shouldReconnect) return@launch
            reconnectAttempts = attempt
            if (serverUrl.isNotEmpty()) {
                connectInternal(serverUrl, deviceId, deviceName, usePublicUrl, publicUrl)
            } else {
                // 没有可用地址，重连无意义，避免状态永远停在 CONNECTING
                _connectionState.value = ConnectionState.DISCONNECTED
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
