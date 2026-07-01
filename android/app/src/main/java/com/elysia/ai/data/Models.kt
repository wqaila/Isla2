package com.elysia.ai.data

import com.google.gson.annotations.SerializedName

// ===== 聊天消息 =====
// 消息状态
enum class MessageStatus {
    SENDING,      // 发送中
    SENT,         // 已发送
    DELIVERED,    // 已送达
    ERROR,        // 发送失败
}

data class ChatMessage(
    val id: String = "",
    val role: String,          // "user" 或 "assistant"
    val content: String,
    val timestamp: Long = System.currentTimeMillis(),
    val tokens: Int = 0,
    val responseMs: Int = 0,
    val isStreaming: Boolean = false,
    val status: MessageStatus = MessageStatus.DELIVERED,
)

// ===== 会话 =====
data class Conversation(
    val id: String,
    val title: String = "",
    val lastMessage: String = "",
    val messageCount: Int = 0,
    val createdAt: Long = System.currentTimeMillis(),
    val updatedAt: Long = System.currentTimeMillis(),
)

// ===== API 请求模型 =====
data class ChatRequest(
    val message: String,
    @SerializedName("session_id")
    val sessionId: String? = null,
    @SerializedName("device_id")
    val deviceId: String = "android",
    @SerializedName("device_name")
    val deviceName: String = "",
    val stream: Boolean = true,
)

data class SessionCreateRequest(
    @SerializedName("device_id")
    val deviceId: String = "android",
    @SerializedName("device_name")
    val deviceName: String = "",
    @SerializedName("connection_type")
    val connectionType: String = "wifi",
)

// ===== API 响应模型 =====
data class SessionResponse(
    @SerializedName("session_id")
    val sessionId: String,
)

data class ServerStatus(
    val server: ServerInfo? = null,
    val ollama: OllamaInfo? = null,
    val stats: ServerStats? = null,
)

data class ServerInfo(
    val status: String = "",
    @SerializedName("local_ip")
    val localIp: String = "",
)

data class OllamaInfo(
    val status: String = "",
    val models: List<String> = emptyList(),
)

data class ServerStats(
    @SerializedName("total_sessions")
    val totalSessions: Int = 0,
    @SerializedName("total_messages")
    val totalMessages: Int = 0,
    @SerializedName("active_sessions")
    val activeSessions: Int = 0,
    @SerializedName("today_messages")
    val todayMessages: Int = 0,
)

// ===== WebSocket 消息模型 =====
data class WsMessage(
    val type: String,          // "chat", "stream", "connected", "heartbeat", etc.
    val message: String? = null,
    val content: String? = null,
    val done: Boolean = false,
    val tokens: Int = 0,
    @SerializedName("response_ms")
    val responseMs: Int = 0,
    @SerializedName("session_id")
    val sessionId: String? = null,
    @SerializedName("connection_id")
    val connectionId: String? = null,
    // 情感分析字段 (type = "emotion")
    val dominant: String? = null,
    val emoji: String? = null,
    val intensity: Float = 0f,
    val valence: Float = 0f,
    val summary: String? = null,
)

// ===== 情感数据 (来自 WebSocket emotion 消息) =====
data class EmotionData(
    val dominant: String,
    val emoji: String,
    val intensity: Float,
    val valence: Float,
    val summary: String,
)

// ===== 连接状态 =====
enum class ConnectionState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    ERROR,
}

// ===== 服务器配置 =====
data class ServerConfig(
    val serverIp: String = "192.168.1.100",
    val serverPort: Int = 8080,
    val connectionType: String = "wifi",  // "wifi" 或 "usb"
    val autoConnect: Boolean = true,
    val usePublicUrl: Boolean = false,    // 是否使用公网地址
    val publicUrl: String = "",           // 公网地址，如 xxx.trycloudflare.com
)
