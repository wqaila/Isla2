package com.elysia.ai

import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.activity.compose.BackHandler
import androidx.compose.animation.*
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.elysia.ai.data.ConnectionState
import com.elysia.ai.data.ServerConfig
import com.elysia.ai.ui.screens.ChatScreen
import com.elysia.ai.ui.screens.SettingsScreen
import com.elysia.ai.ui.theme.ElysiaAITheme
import com.elysia.ai.viewmodel.ChatViewModel
import kotlinx.coroutines.flow.collectLatest

class MainActivity : ComponentActivity() {

    private val viewModel: ChatViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            val isDarkMode by viewModel.isDarkMode.collectAsStateWithLifecycle()
            
            ElysiaAITheme(darkTheme = isDarkMode) {
                ElysiaApp(viewModel)
            }
        }
    }
}

@Composable
fun ElysiaApp(viewModel: ChatViewModel) {
    val context = LocalContext.current

    // 收集状态
    val connectionState by viewModel.connectionState.collectAsStateWithLifecycle()
    val serverConfig by viewModel.serverConfig.collectAsStateWithLifecycle()
    val isDarkMode by viewModel.isDarkMode.collectAsStateWithLifecycle()

    // 当前页面
    var currentScreen by remember { mutableStateOf("chat") }

    // 监听错误消息
    LaunchedEffect(Unit) {
        viewModel.errorMessage.collectLatest { error ->
            Toast.makeText(context, error, Toast.LENGTH_SHORT).show()
        }
    }

    // 系统返回键处理：在 SettingsScreen 返回聊天页，在聊天页退出应用
    BackHandler(enabled = currentScreen == "settings") {
        currentScreen = "chat"
    }

    when (currentScreen) {
        "chat" -> {
            ChatScreen(
                viewModel = viewModel,
                onOpenSettings = { currentScreen = "settings" }
            )
        }

        "settings" -> {
            SettingsScreen(
                config = serverConfig,
                connectionState = connectionState,
                isDarkMode = isDarkMode,
                onSave = { config ->
                    viewModel.updateConfig(config)
                },
                onConnect = {
                    viewModel.connect()
                    currentScreen = "chat"
                },
                onDisconnect = {
                    viewModel.disconnect()
                },
                onBack = { currentScreen = "chat" },
                onToggleDarkMode = { viewModel.toggleDarkMode() }
            )
        }
    }
}
