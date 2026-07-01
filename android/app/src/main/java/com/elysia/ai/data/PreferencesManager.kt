package com.elysia.ai.data

import android.content.Context
import android.content.SharedPreferences
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken

/**
 * SharedPreferences 持久化管理器
 * 负责保存/加载服务器配置和聊天记录
 */
class PreferencesManager(context: Context) {
    
    private val prefs: SharedPreferences = context.getSharedPreferences("elysia_prefs", Context.MODE_PRIVATE)
    private val gson = Gson()
    
    companion object {
        private const val KEY_SERVER_IP = "server_ip"
        private const val KEY_SERVER_PORT = "server_port"
        private const val KEY_CONNECTION_TYPE = "connection_type"
        private const val KEY_AUTO_CONNECT = "auto_connect"
        private const val KEY_USE_PUBLIC_URL = "use_public_url"
        private const val KEY_PUBLIC_URL = "public_url"
        private const val KEY_SESSION_ID = "session_id"
        private const val KEY_MESSAGES = "messages"
        private const val KEY_DARK_MODE = "dark_mode"
    }
    
    // ===== 服务器配置 =====
    
    fun saveServerConfig(config: ServerConfig) {
        prefs.edit().apply {
            putString(KEY_SERVER_IP, config.serverIp)
            putInt(KEY_SERVER_PORT, config.serverPort)
            putString(KEY_CONNECTION_TYPE, config.connectionType)
            putBoolean(KEY_AUTO_CONNECT, config.autoConnect)
            putBoolean(KEY_USE_PUBLIC_URL, config.usePublicUrl)
            putString(KEY_PUBLIC_URL, config.publicUrl)
            apply()
        }
    }
    
    fun loadServerConfig(): ServerConfig {
        return ServerConfig(
            serverIp = prefs.getString(KEY_SERVER_IP, "192.168.1.100") ?: "192.168.1.100",
            serverPort = prefs.getInt(KEY_SERVER_PORT, 8080),
            connectionType = prefs.getString(KEY_CONNECTION_TYPE, "wifi") ?: "wifi",
            autoConnect = prefs.getBoolean(KEY_AUTO_CONNECT, true),
            usePublicUrl = prefs.getBoolean(KEY_USE_PUBLIC_URL, false),
            publicUrl = prefs.getString(KEY_PUBLIC_URL, "") ?: "",
        )
    }
    
    // ===== 会话 ID =====
    
    fun saveSessionId(sessionId: String) {
        prefs.edit().putString(KEY_SESSION_ID, sessionId).apply()
    }
    
    fun loadSessionId(): String? {
        return prefs.getString(KEY_SESSION_ID, null)
    }
    
    // ===== 聊天记录持久化 =====
    
    fun saveMessages(messages: List<ChatMessage>) {
        // 最多保存 500 条消息，防止 SharedPreferences 过大
        val toSave = if (messages.size > 500) messages.takeLast(500) else messages
        val json = gson.toJson(toSave)
        prefs.edit().putString(KEY_MESSAGES, json).apply()
    }
    
    fun loadMessages(): List<ChatMessage> {
        val json = prefs.getString(KEY_MESSAGES, null) ?: return emptyList()
        return try {
            val type = object : TypeToken<List<ChatMessage>>() {}.type
            gson.fromJson(json, type) ?: emptyList()
        } catch (e: Exception) {
            emptyList()
        }
    }
    
    fun clearMessages() {
        prefs.edit().remove(KEY_MESSAGES).apply()
    }
    
    // ===== 主题设置 =====
    
    fun saveDarkMode(isDark: Boolean) {
        prefs.edit().putBoolean(KEY_DARK_MODE, isDark).apply()
    }
    
    fun loadDarkMode(): Boolean {
        return prefs.getBoolean(KEY_DARK_MODE, true) // 默认暗色主题
    }
}
