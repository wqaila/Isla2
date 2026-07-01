package com.elysia.ai.ui.screens

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.elysia.ai.data.ConnectionState
import com.elysia.ai.data.ServerConfig
import com.elysia.ai.ui.theme.*
import kotlinx.coroutines.delay

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    config: ServerConfig,
    connectionState: ConnectionState,
    isDarkMode: Boolean = true,
    onSave: (ServerConfig) -> Unit,
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
    onBack: () -> Unit,
    onToggleDarkMode: () -> Unit = {},
    onTestConnection: ((ServerConfig) -> Unit)? = null,
    modifier: Modifier = Modifier
) {
    var ip by remember(config) { mutableStateOf(config.serverIp) }
    var port by remember(config) { mutableStateOf(config.serverPort.toString()) }
    var connectionType by remember(config) { mutableStateOf(config.connectionType) }
    var autoConnect by remember(config) { mutableStateOf(config.autoConnect) }
    var usePublicUrl by remember(config) { mutableStateOf(config.usePublicUrl) }
    var publicUrl by remember(config) { mutableStateOf(config.publicUrl) }

    // 验证状态
    var ipError by remember { mutableStateOf<String?>(null) }
    var portError by remember { mutableStateOf<String?>(null) }
    var urlError by remember { mutableStateOf<String?>(null) }
    var showSaveSuccess by remember { mutableStateOf(false) }

    // 测试连接状态
    var isTesting by remember { mutableStateOf(false) }
    var testResult by remember { mutableStateOf<TestResult?>(null) }

    // 验证函数
    fun validateIp(): Boolean {
        if (usePublicUrl) return true
        val trimmed = ip.trim()
        if (trimmed.isEmpty()) { ipError = "IP 地址不能为空"; return false }
        if (trimmed == "localhost" || trimmed == "127.0.0.1") {
            ipError = null; return true
        }
        val ipPattern = Regex("""^(\d{1,3}\.){3}\d{1,3}$""")
        if (!ipPattern.matches(trimmed)) {
            ipError = "IP 格式不正确 (如 192.168.1.100)"
            return false
        }
        ipError = null
        return true
    }

    fun validatePort(): Boolean {
        if (usePublicUrl) return true
        val portNum = port.toIntOrNull()
        if (portNum == null || portNum < 1 || portNum > 65535) {
            portError = "端口范围 1-65535"
            return false
        }
        portError = null
        return true
    }

    fun validateUrl(): Boolean {
        if (!usePublicUrl) return true
        val trimmed = publicUrl.trim()
        if (trimmed.isEmpty()) { urlError = "公网地址不能为空"; return false }
        if (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")) {
            urlError = "地址需以 http:// 或 https:// 开头"
            return false
        }
        urlError = null
        return true
    }

    fun buildConfig() = ServerConfig(
        serverIp = ip.trim(),
        serverPort = port.toIntOrNull() ?: 8080,
        connectionType = connectionType,
        autoConnect = autoConnect,
        usePublicUrl = usePublicUrl,
        publicUrl = publicUrl.trim()
    )

    fun doSave() {
        if (!validateIp() || !validatePort() || !validateUrl()) return
        onSave(buildConfig())
        showSaveSuccess = true
    }

    fun doTest() {
        if (!validateIp() || !validatePort() || !validateUrl()) return
        val cfg = buildConfig()
        // 先保存
        onSave(cfg)
        // 模拟测试 (实际应调用 ViewModel 方法)
        isTesting = true
        testResult = null
        onTestConnection?.invoke(cfg)
    }

    // 保存成功提示自动消失
    LaunchedEffect(showSaveSuccess) {
        if (showSaveSuccess) {
            delay(2000)
            showSaveSuccess = false
        }
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    0.0f to ElysiaPurpleDeep,
                    0.3f to ElysiaPurpleSurface,
                    1.0f to ElysiaBackground
                )
            )
    ) {
        // ===== 顶部栏 =====
        TopAppBar(
            title = {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("⚙️", fontSize = 20.sp)
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        text = "设置",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                }
            },
            navigationIcon = {
                IconButton(onClick = onBack) {
                    Icon(
                        Icons.Filled.ArrowBack,
                        contentDescription = "返回",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            },
            actions = {
                // 保存成功提示
                AnimatedVisibility(
                    visible = showSaveSuccess,
                    enter = fadeIn(tween(300)) + scaleIn(tween(300)),
                    exit = fadeOut(tween(300)) + scaleOut(tween(300))
                ) {
                    Surface(
                        color = StatusOnline.copy(alpha = 0.12f),
                        shape = RoundedCornerShape(20.dp),
                        modifier = Modifier.padding(end = 8.dp)
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(
                                Icons.Default.CheckCircle,
                                null,
                                tint = StatusOnline,
                                modifier = Modifier.size(16.dp)
                            )
                            Spacer(modifier = Modifier.width(4.dp))
                            Text(
                                "已保存",
                                style = MaterialTheme.typography.labelSmall,
                                color = StatusOnline,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                    }
                }
            },
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.85f)
            )
        )

        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            // ===== 连接状态卡片 =====
            StatusCard(connectionState = connectionState)

            // ===== 服务器配置 =====
            SettingsCard(
                title = "🖥️ 服务器配置",
                titleColor = ElysiaPink
            ) {
                // IP 地址
                OutlinedTextField(
                    value = ip,
                    onValueChange = { ip = it; ipError = null },
                    label = { Text("服务器 IP", color = TextSecondary) },
                    placeholder = { Text("192.168.1.100", color = TextSecondary.copy(alpha = 0.35f)) },
                    leadingIcon = {
                        Icon(Icons.Default.Language, null, tint = ElysiaCyan)
                    },
                    isError = ipError != null,
                    supportingText = ipError?.let { { Text(it, color = StatusOffline) } },
                    colors = fieldColors(),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    enabled = !usePublicUrl
                )

                // 端口
                OutlinedTextField(
                    value = port,
                    onValueChange = { port = it.filter { c -> c.isDigit() }; portError = null },
                    label = { Text("端口", color = TextSecondary) },
                    placeholder = { Text("8080", color = TextSecondary.copy(alpha = 0.35f)) },
                    leadingIcon = {
                        Icon(Icons.Default.Tag, null, tint = ElysiaCyan)
                    },
                    isError = portError != null,
                    supportingText = portError?.let { { Text(it, color = StatusOffline) } },
                    colors = fieldColors(),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    enabled = !usePublicUrl
                )
            }

            // ===== 公网访问 =====
            SettingsCard(
                title = "🌐 公网访问（Cloudflare Tunnel）",
                titleColor = ElysiaCyan
            ) {
                // 启用公网地址开关
                SettingsToggle(
                    title = "启用公网地址",
                    description = "通过 Cloudflare Tunnel 远程访问",
                    checked = usePublicUrl,
                    onCheckedChange = {
                        usePublicUrl = it
                        urlError = null
                    },
                    activeColor = ElysiaCyan
                )

                // 公网地址输入
                AnimatedVisibility(
                    visible = usePublicUrl,
                    enter = expandVertically(animationSpec = tween(300)) + fadeIn(tween(300)),
                    exit = shrinkVertically(animationSpec = tween(200)) + fadeOut(tween(200))
                ) {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(
                            value = publicUrl,
                            onValueChange = { publicUrl = it; urlError = null },
                            label = { Text("公网地址", color = TextSecondary) },
                            placeholder = {
                                Text(
                                    "https://xxx.trycloudflare.com",
                                    color = TextSecondary.copy(alpha = 0.35f)
                                )
                            },
                            leadingIcon = {
                                Icon(Icons.Default.Language, null, tint = ElysiaCyan)
                            },
                            isError = urlError != null,
                            supportingText = urlError?.let { { Text(it, color = StatusOffline) } },
                            colors = fieldColors(focusColor = ElysiaCyan),
                            shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.fillMaxWidth(),
                            singleLine = true
                        )

                        Surface(
                            color = ElysiaCyan.copy(alpha = 0.08f),
                            shape = RoundedCornerShape(10.dp)
                        ) {
                            Text(
                                text = "💡 在 PC 端运行 start_tunnel.bat 后，\n将终端输出的 https://xxx.trycloudflare.com\n粘贴到上方输入框",
                                style = MaterialTheme.typography.bodySmall,
                                color = ElysiaCyan.copy(alpha = 0.7f),
                                modifier = Modifier.padding(12.dp)
                            )
                        }
                    }
                }
            }

            // ===== 连接方式 =====
            SettingsCard(
                title = "🔗 连接方式",
                titleColor = ElysiaPink
            ) {
                // WiFi / USB 选择
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    ConnectionChip(
                        selected = connectionType == "wifi",
                        onClick = { connectionType = "wifi" },
                        icon = Icons.Default.Wifi,
                        label = "WiFi",
                        selectedColor = ElysiaPink
                    )
                    ConnectionChip(
                        selected = connectionType == "usb",
                        onClick = { connectionType = "usb" },
                        icon = Icons.Default.Usb,
                        label = "USB",
                        selectedColor = ElysiaCyan
                    )
                }

                Spacer(modifier = Modifier.height(4.dp))

                // 自动连接
                SettingsToggle(
                    title = "自动连接",
                    description = "启动应用时自动连接服务器",
                    checked = autoConnect,
                    onCheckedChange = { autoConnect = it },
                    activeColor = ElysiaPink
                )

                // 夜间模式
                Spacer(modifier = Modifier.height(4.dp))
                SettingsToggle(
                    title = "深色模式",
                    description = if (isDarkMode) "已启用夜间模式" else "已启用日间模式",
                    checked = isDarkMode,
                    onCheckedChange = { onToggleDarkMode() },
                    activeColor = ElysiaPurple,
                    icon = if (isDarkMode) Icons.Default.DarkMode else Icons.Default.LightMode
                )
            }

            // ===== 测试连接 =====
            if (onTestConnection != null) {
                SettingsCard(
                    title = "🔍 连接测试",
                    titleColor = ElysiaGold
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                "测试服务器连通性",
                                color = TextPrimary,
                                style = MaterialTheme.typography.bodyMedium
                            )
                            Text(
                                "验证配置是否可用",
                                style = MaterialTheme.typography.bodySmall,
                                color = TextSecondary
                            )
                        }
                        FilledTonalButton(
                            onClick = { doTest() },
                            enabled = !isTesting,
                            colors = ButtonDefaults.filledTonalButtonColors(
                                containerColor = ElysiaGold.copy(alpha = 0.15f),
                                contentColor = ElysiaGold
                            ),
                            shape = RoundedCornerShape(12.dp)
                        ) {
                            if (isTesting) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(16.dp),
                                    color = ElysiaGold,
                                    strokeWidth = 1.5.dp
                                )
                                Spacer(modifier = Modifier.width(6.dp))
                                Text("测试中...")
                            } else {
                                Icon(Icons.Default.NetworkCheck, null, modifier = Modifier.size(18.dp))
                                Spacer(modifier = Modifier.width(6.dp))
                                Text("测试连接")
                            }
                        }
                    }

                    // 测试结果
                    testResult?.let { result ->
                        Spacer(modifier = Modifier.height(10.dp))
                        val bgColor = if (result.success) StatusOnline.copy(alpha = 0.1f) else StatusOffline.copy(alpha = 0.1f)
                        val fgColor = if (result.success) StatusOnline else StatusOffline
                        val icon = if (result.success) Icons.Default.CheckCircle else Icons.Default.Cancel
                        Surface(
                            color = bgColor,
                            shape = RoundedCornerShape(10.dp)
                        ) {
                            Row(
                                modifier = Modifier.padding(12.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Icon(icon, null, tint = fgColor, modifier = Modifier.size(18.dp))
                                Spacer(modifier = Modifier.width(8.dp))
                                Text(
                                    result.message,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = fgColor,
                                    fontWeight = FontWeight.SemiBold
                                )
                            }
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(4.dp))

            // ===== 操作按钮 =====
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                // 保存按钮
                OutlinedButton(
                    onClick = { doSave() },
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = ElysiaPink
                    ),
                    border = ButtonDefaults.outlinedButtonBorder.copy(
                        brush = androidx.compose.ui.graphics.SolidColor(ElysiaPink.copy(alpha = 0.5f))
                    ),
                    shape = RoundedCornerShape(14.dp)
                ) {
                    Icon(Icons.Default.Save, null, modifier = Modifier.size(18.dp))
                    Spacer(modifier = Modifier.width(6.dp))
                    Text("保存配置", fontWeight = FontWeight.SemiBold)
                }

                // 连接/断开按钮
                Button(
                    onClick = {
                        doSave()
                        if (connectionState == ConnectionState.CONNECTED) {
                            onDisconnect()
                        } else {
                            onConnect()
                        }
                    },
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (connectionState == ConnectionState.CONNECTED)
                            StatusOffline else ElysiaPink
                    ),
                    shape = RoundedCornerShape(14.dp),
                    elevation = ButtonDefaults.buttonElevation(defaultElevation = 4.dp)
                ) {
                    Icon(
                        if (connectionState == ConnectionState.CONNECTED)
                            Icons.Default.LinkOff else Icons.Default.Link,
                        null,
                        modifier = Modifier.size(18.dp)
                    )
                    Spacer(modifier = Modifier.width(6.dp))
                    Text(
                        if (connectionState == ConnectionState.CONNECTED) "断开连接" else "连接服务器",
                        fontWeight = FontWeight.SemiBold
                    )
                }
            }

            // ===== 使用说明 =====
            SettingsCard(
                title = "💡 使用说明",
                titleColor = ElysiaGold
            ) {
                InstructionRow(
                    icon = "①",
                    text = "确保电脑端服务已启动（运行 start_server.bat）"
                )
                Spacer(modifier = Modifier.height(6.dp))
                InstructionRow(
                    icon = "②",
                    text = "手机和电脑需在同一 WiFi 网络下"
                )
                Spacer(modifier = Modifier.height(6.dp))
                InstructionRow(
                    icon = "③",
                    text = "输入电脑的 IP 地址和端口号"
                )
                Spacer(modifier = Modifier.height(6.dp))
                InstructionRow(
                    icon = "④",
                    text = "点击「连接服务器」即可开始聊天"
                )

                Spacer(modifier = Modifier.height(10.dp))

                Surface(
                    color = ElysiaPurpleCard.copy(alpha = 0.6f),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text(
                            "USB 连接方式：",
                            style = MaterialTheme.typography.labelSmall,
                            color = ElysiaPurpleLight,
                            fontWeight = FontWeight.SemiBold
                        )
                        Text(
                            "需先在电脑终端执行 ADB 端口转发：\nadb reverse tcp:8080 tcp:8080\n然后 IP 填写 127.0.0.1 即可",
                            style = MaterialTheme.typography.bodySmall,
                            color = TextSecondary.copy(alpha = 0.8f)
                        )
                    }
                }
            }

            // ===== 关于 =====
            SettingsCard(
                title = "📱 关于",
                titleColor = ElysiaPurpleLight
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Text(
                            "爱莉希雅 AI",
                            style = MaterialTheme.typography.titleSmall,
                            color = TextPrimary,
                            fontWeight = FontWeight.Bold
                        )
                        Text(
                            "Elysia AI Chat v1.0.0",
                            style = MaterialTheme.typography.bodySmall,
                            color = TextSecondary
                        )
                    }
                    // 小爱心图标
                    Box(
                        modifier = Modifier
                            .size(40.dp)
                            .clip(CircleShape)
                            .background(
                                Brush.linearGradient(listOf(ElysiaPink, ElysiaPinkDark))
                            ),
                        contentAlignment = Alignment.Center
                    ) {
                        Text("💖", fontSize = 19.sp)
                    }
                }
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    "基于 Compose + Ktor 构建的 AI 聊天应用\n支持 WebSocket 实时通信与情感感知",
                    style = MaterialTheme.typography.bodySmall,
                    color = TextSecondary.copy(alpha = 0.7f),
                    lineHeight = 18.sp
                )
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    "Made with 💖 by Elysia Team",
                    style = MaterialTheme.typography.labelSmall,
                    color = ElysiaPinkLight.copy(alpha = 0.6f),
                    modifier = Modifier.fillMaxWidth(),
                    textAlign = TextAlign.Center
                )
            }

            // 底部留白
            Spacer(modifier = Modifier.height(24.dp))
        }
    }
}

// ============================================================
// 可复用组件
// ============================================================

@Composable
private fun StatusCard(connectionState: ConnectionState) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = ElysiaPurpleCard.copy(alpha = 0.5f)
        ),
        shape = RoundedCornerShape(16.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // 状态指示灯 — 带脉冲动画
            val isConnected = connectionState == ConnectionState.CONNECTED
            val isConnecting = connectionState == ConnectionState.CONNECTING
            val pulseScale by rememberInfiniteTransition().animateFloat(
                initialValue = 1f,
                targetValue = if (isConnected || isConnecting) 1.3f else 1f,
                animationSpec = infiniteRepeatable(
                    animation = tween(800, easing = FastOutSlowInEasing),
                    repeatMode = RepeatMode.Reverse
                ),
                label = "statusPulse"
            )
            Box(
                modifier = Modifier
                    .size(14.dp)
                    .scale(pulseScale)
                    .clip(CircleShape)
                    .background(
                        when (connectionState) {
                            ConnectionState.CONNECTED -> StatusOnline
                            ConnectionState.CONNECTING -> StatusConnecting
                            ConnectionState.ERROR -> StatusOffline
                            ConnectionState.DISCONNECTED -> TextSecondary
                        }
                    )
            )
            Spacer(modifier = Modifier.width(12.dp))
            Column {
                Text(
                    text = "连接状态",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = TextPrimary
                )
                Text(
                    text = when (connectionState) {
                        ConnectionState.CONNECTED -> "已连接 ✦ 可以开始对话"
                        ConnectionState.CONNECTING -> "连接中..."
                        ConnectionState.ERROR -> "连接失败，请检查配置"
                        ConnectionState.DISCONNECTED -> "未连接"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = when (connectionState) {
                        ConnectionState.CONNECTED -> StatusOnline
                        ConnectionState.CONNECTING -> StatusConnecting
                        ConnectionState.ERROR -> StatusOffline
                        ConnectionState.DISCONNECTED -> TextSecondary
                    }
                )
            }
        }
    }
}

@Composable
private fun SettingsCard(
    title: String,
    titleColor: Color = ElysiaPink,
    content: @Composable ColumnScope.() -> Unit
) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = ElysiaPurpleCard.copy(alpha = 0.35f)
        ),
        shape = RoundedCornerShape(16.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp)
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text(
                text = title,
                style = MaterialTheme.typography.titleMedium,
                color = titleColor,
                fontWeight = FontWeight.Bold
            )
            content()
        }
    }
}

@Composable
private fun SettingsToggle(
    title: String,
    description: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    activeColor: Color = ElysiaPink,
    icon: ImageVector? = null
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Row(
            modifier = Modifier.weight(1f),
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (icon != null) {
                Icon(
                    icon,
                    null,
                    tint = if (checked) activeColor else TextSecondary,
                    modifier = Modifier.size(20.dp)
                )
                Spacer(modifier = Modifier.width(8.dp))
            }
            Column {
                Text(title, color = TextPrimary, style = MaterialTheme.typography.bodyMedium)
                Text(
                    description,
                    style = MaterialTheme.typography.bodySmall,
                    color = TextSecondary
                )
            }
        }
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            colors = SwitchDefaults.colors(
                checkedThumbColor = activeColor,
                checkedTrackColor = activeColor.copy(alpha = 0.3f),
                uncheckedThumbColor = TextSecondary.copy(alpha = 0.5f),
                uncheckedTrackColor = TextSecondary.copy(alpha = 0.15f)
            )
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ConnectionChip(
    selected: Boolean,
    onClick: () -> Unit,
    icon: ImageVector,
    label: String,
    selectedColor: Color
) {
    FilterChip(
        selected = selected,
        onClick = onClick,
        label = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(icon, null, modifier = Modifier.size(16.dp))
                Spacer(modifier = Modifier.width(6.dp))
                Text(label, fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal)
            }
        },
        leadingIcon = if (selected) {
            { Icon(Icons.Default.Check, null, modifier = Modifier.size(16.dp)) }
        } else null,
        colors = FilterChipDefaults.filterChipColors(
            selectedContainerColor = selectedColor.copy(alpha = 0.2f),
            selectedLabelColor = selectedColor,
            containerColor = ElysiaSurfaceVariant,
            labelColor = TextSecondary
        ),
        shape = RoundedCornerShape(10.dp)
    )
}

@Composable
private fun InstructionRow(icon: String, text: String) {
    Row(
        verticalAlignment = Alignment.Top
    ) {
        Text(
            text = icon,
            color = ElysiaGold,
            fontWeight = FontWeight.Bold,
            fontSize = 15.sp,
            modifier = Modifier.width(24.dp)
        )
        Text(
            text = text,
            style = MaterialTheme.typography.bodyMedium,
            color = TextPrimary.copy(alpha = 0.85f)
        )
    }
}

@Composable
private fun fieldColors(focusColor: Color = ElysiaPink) = OutlinedTextFieldDefaults.colors(
    focusedTextColor = TextPrimary,
    unfocusedTextColor = TextPrimary,
    disabledTextColor = TextSecondary.copy(alpha = 0.5f),
    focusedBorderColor = focusColor.copy(alpha = 0.5f),
    unfocusedBorderColor = TextSecondary.copy(alpha = 0.2f),
    disabledBorderColor = TextSecondary.copy(alpha = 0.1f),
    cursorColor = focusColor,
    focusedContainerColor = ElysiaSurfaceVariant.copy(alpha = 0.4f),
    unfocusedContainerColor = ElysiaSurfaceVariant.copy(alpha = 0.2f),
)

// ============================================================
// 数据类
// ============================================================

data class TestResult(
    val success: Boolean,
    val message: String
)