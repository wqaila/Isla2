package com.elysia.ai.ui.screens

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.KeyboardVoice
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import kotlinx.coroutines.launch
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.elysia.ai.data.ConnectionState
import com.elysia.ai.data.MessageStatus
import com.elysia.ai.data.ChatMessage
import com.elysia.ai.ui.theme.*
import com.elysia.ai.viewmodel.ChatViewModel
import java.text.SimpleDateFormat
import java.util.*

// ============================================================
// 主聊天界面 — 爱莉希雅 AI
// 设计系统：12dp 圆角规范 / 8pt 间距网格
// ============================================================

private val CORNER_LARGE = 24.dp    // 卡片/大面板
private val CORNER_MEDIUM = 12.dp   // 气泡/标准组件
private val CORNER_SMALL = 8.dp     // 标签/小元素

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(viewModel: ChatViewModel, onOpenSettings: () -> Unit) {
    val messages by viewModel.messages.collectAsState()
    val connectionState by viewModel.connectionState.collectAsState()
    val isStreaming by viewModel.isStreaming.collectAsState()
    val emotion by viewModel.emotion.collectAsState()
    val context = LocalContext.current

    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    var inputText by remember { mutableStateOf("") }
    var showStatusBanner by remember { mutableStateOf(false) }
    var statusText by remember { mutableStateOf("") }
    var statusType by remember { mutableStateOf(0) }
    var showScrollToBottom by remember { mutableStateOf(false) }
    var showEmotionBanner by remember { mutableStateOf(false) }

    // 情绪变化时短暂高亮（仅当 intensity > 0 时展示，持续 5 秒后自动收起）
    LaunchedEffect(emotion) {
        val e = emotion
        if (e != null && e.intensity > 0f && e.dominant.isNotBlank()) {
            showEmotionBanner = true
            kotlinx.coroutines.delay(5000)
            showEmotionBanner = false
        }
    }

    // 自动滚动到底部
    LaunchedEffect(messages.size, isStreaming) {
        if (messages.isNotEmpty()) {
            listState.animateScrollToItem(messages.size - 1)
        }
    }

    // 监听是否显示"滚动到底部"按钮
    LaunchedEffect(listState.firstVisibleItemIndex, listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index) {
        val lastVisible = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
        showScrollToBottom = messages.size > 1 && lastVisible < messages.size - 2
    }

    // 错误提示
    LaunchedEffect(Unit) {
        viewModel.errorMessage.collect { msg ->
            Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
        }
    }

    // 连接状态横幅
    LaunchedEffect(connectionState) {
        when (connectionState) {
            ConnectionState.CONNECTING -> {
                showStatusBanner = true; statusText = "正在连接服务器..."; statusType = 2
            }
            ConnectionState.CONNECTED -> {
                showStatusBanner = true; statusText = "已连接到爱莉希雅"; statusType = 1
                kotlinx.coroutines.delay(3000)
                if (connectionState == ConnectionState.CONNECTED) showStatusBanner = false
            }
            ConnectionState.ERROR -> {
                showStatusBanner = true; statusText = "连接失败，请检查服务器地址"; statusType = 3
            }
            ConnectionState.DISCONNECTED -> {
                showStatusBanner = false
            }
        }
    }

    val dateFormat = remember { SimpleDateFormat("HH:mm", Locale.getDefault()) }
    val dateHeaderFormat = remember { SimpleDateFormat("MM月dd日 EEEE", Locale.CHINESE) }

    Box(modifier = Modifier.fillMaxSize()) {
        // 背景渐变
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    Brush.verticalGradient(
                        0.0f to ElysiaPurpleDeep,
                        0.4f to ElysiaPurpleSurface,
                        1.0f to ElysiaBackground
                    )
                )
        )

        Column(modifier = Modifier.fillMaxSize()) {
            // ===== 顶部栏（精简版：仅头像+名称+状态+设置） =====
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        // 头像 — 脉动光环
                        Box(
                            modifier = Modifier
                                .size(36.dp)
                                .clip(CircleShape)
                                .background(
                                    Brush.linearGradient(listOf(ElysiaPink, ElysiaPinkDark))
                                ),
                            contentAlignment = Alignment.Center
                        ) {
                            if (connectionState == ConnectionState.CONNECTED) {
                                val pulseScale by rememberInfiniteTransition().animateFloat(
                                    initialValue = 1f, targetValue = 1.15f,
                                    animationSpec = infiniteRepeatable(
                                        animation = tween(1200, easing = FastOutSlowInEasing),
                                        repeatMode = RepeatMode.Reverse
                                    ), label = "avatarPulse"
                                )
                                Box(
                                    modifier = Modifier
                                        .size(36.dp).scale(pulseScale)
                                        .clip(CircleShape)
                                        .border(1.5.dp, StatusOnline.copy(alpha = 0.4f), CircleShape)
                                )
                            }
                            Text("💖", fontSize = 17.sp)
                        }
                        Spacer(modifier = Modifier.width(8.dp))
                        Column {
                            Text(
                                text = "爱莉希雅",
                                style = MaterialTheme.typography.titleSmall,
                                fontWeight = FontWeight.Bold,
                                color = MaterialTheme.colorScheme.onSurface
                            )
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Box(
                                    modifier = Modifier
                                        .size(6.dp).clip(CircleShape)
                                        .background(
                                            when (connectionState) {
                                                ConnectionState.CONNECTED -> StatusOnline
                                                ConnectionState.CONNECTING -> StatusConnecting
                                                ConnectionState.ERROR -> StatusOffline
                                                ConnectionState.DISCONNECTED -> TextMuted
                                            }
                                        )
                                )
                                Spacer(modifier = Modifier.width(4.dp))
                                Text(
                                    text = when (connectionState) {
                                        ConnectionState.CONNECTED -> if (isStreaming) "正在输入..." else "在线"
                                        ConnectionState.CONNECTING -> "连接中..."
                                        ConnectionState.ERROR -> "连接失败"
                                        ConnectionState.DISCONNECTED -> "离线"
                                    },
                                    style = MaterialTheme.typography.labelSmall,
                                    color = when (connectionState) {
                                        ConnectionState.CONNECTED -> StatusOnline
                                        ConnectionState.CONNECTING -> StatusConnecting
                                        ConnectionState.ERROR -> StatusOffline
                                        ConnectionState.DISCONNECTED -> TextMuted
                                    }
                                )
                            }
                        }
                    }
                },
                actions = {
                    IconButton(onClick = onOpenSettings) {
                        Icon(
                            Icons.Default.Settings,
                            contentDescription = "设置",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.85f)
                )
            )

            // ===== 连接状态横幅 (动态) =====
            AnimatedVisibility(
                visible = showStatusBanner,
                enter = expandVertically(expandFrom = Alignment.Top) + fadeIn(tween(300)),
                exit = shrinkVertically(shrinkTowards = Alignment.Top) + fadeOut(tween(400))
            ) {
                val bannerBg = when (statusType) {
                    1 -> StatusOnline.copy(alpha = 0.1f)
                    2 -> StatusConnecting.copy(alpha = 0.1f)
                    3 -> StatusOffline.copy(alpha = 0.1f)
                    else -> ElysiaCyan.copy(alpha = 0.1f)
                }
                val bannerFg = when (statusType) {
                    1 -> StatusOnline; 2 -> StatusConnecting; 3 -> StatusOffline; else -> ElysiaCyan
                }
                Surface(color = bannerBg, modifier = Modifier.fillMaxWidth()) {
                    Row(
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        if (statusType == 2) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(12.dp),
                                color = bannerFg,
                                strokeWidth = 1.5.dp
                            )
                        } else {
                            Icon(
                                imageVector = when (statusType) {
                                    1 -> Icons.Default.CheckCircle
                                    3 -> Icons.Default.Error
                                    else -> Icons.Default.Info
                                },
                                contentDescription = null,
                                modifier = Modifier.size(12.dp),
                                tint = bannerFg
                            )
                        }
                        Spacer(modifier = Modifier.width(6.dp))
                        Text(
                            text = statusText,
                            style = MaterialTheme.typography.bodySmall,
                            color = bannerFg
                        )
                    }
                }
            }

            // ===== 情绪变化横幅（仅在检测到情绪变化时短暂展示） =====
            AnimatedVisibility(
                visible = showEmotionBanner,
                enter = expandVertically(expandFrom = Alignment.Top) + fadeIn(tween(300)),
                exit = shrinkVertically(shrinkTowards = Alignment.Top) + fadeOut(tween(400))
            ) {
                emotion?.let { em ->
                    Surface(
                        color = ElysiaPink.copy(alpha = 0.08f),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(text = em.emoji, fontSize = 14.sp)
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(
                                text = "情绪: ${em.summary.ifEmpty { em.dominant }} · 强度 ${(em.intensity * 100).toInt()}%",
                                style = MaterialTheme.typography.labelSmall,
                                color = ElysiaPinkLight,
                                fontWeight = FontWeight.Medium
                            )
                            Spacer(modifier = Modifier.weight(1f))
                            IconButton(
                                onClick = { showEmotionBanner = false },
                                modifier = Modifier.size(20.dp)
                            ) {
                                Icon(
                                    Icons.Default.Close,
                                    contentDescription = "关闭",
                                    tint = ElysiaPinkLight,
                                    modifier = Modifier.size(14.dp)
                                )
                            }
                        }
                    }
                }
            }

            // ===== 消息列表 =====
            Box(modifier = Modifier.weight(1f)) {
                // 构建扁平消息列表，AI 连续消息仅首条显示头像
                val flatItems = remember(messages) {
                    val result = mutableListOf<Any>()
                    var lastDate: String? = null
                    var lastRole: String? = null
                    for (message in messages) {
                        val msgDate = dateHeaderFormat.format(Date(message.timestamp))
                        if (msgDate != lastDate) {
                            result.add("date_$msgDate" to msgDate)
                            lastDate = msgDate
                            lastRole = null // 日期重置后重新判断
                        }
                        // 标记是否需要显示头像：用户消息始终显示；AI 消息仅当上一条不是 AI 时显示
                        val showAvatar = if (message.role == "user") {
                            true
                        } else {
                            lastRole != "assistant"
                        }
                        result.add(Pair(message, showAvatar))
                        lastRole = message.role
                    }
                    result
                }

                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(2.dp)
                ) {
                    if (messages.isEmpty()) {
                        item { WelcomePlaceholder() }
                    }

                    items(flatItems, key = { item ->
                        when (item) {
                            is Pair<*, *> -> {
                                // Pair<String, String> for date headers
                                if (item.first is String && item.second is String && (item.first as String).startsWith("date_"))
                                    item.first as String
                                // Pair<ChatMessage, Boolean> for messages
                                else (item.first as ChatMessage).id
                            }
                            else -> item.hashCode()
                        }
                    }) { item ->
                        when {
                            item is Pair<*, *> && item.first is String && item.second is String && (item.first as String).startsWith("date_") ->
                                DateSeparator(text = item.second as String)
                            item is Pair<*, *> && item.first is ChatMessage -> {
                                val msg = item.first as ChatMessage
                                val showAv = item.second as Boolean
                                MessageItem(
                                    message = msg,
                                    dateFormat = dateFormat,
                                    showAvatar = showAv,
                                    onResend = { viewModel.resendMessage(msg.id) },
                                    onRegenerate = { viewModel.regenerateResponse(msg.id) }
                                )
                            }
                        }
                    }

                    if (isStreaming) {
                        val lastMsg = messages.lastOrNull()
                        if (lastMsg == null || (lastMsg.role == "assistant" && lastMsg.isStreaming && lastMsg.content.isEmpty())) {
                            item { TypingIndicator() }
                        }
                    }
                }

                // 滚动到底部 FAB
                if (showScrollToBottom && messages.size > 1) {
                    val fabAlpha by animateFloatAsState(
                        targetValue = if (showScrollToBottom && messages.size > 1) 1f else 0f,
                        animationSpec = tween(200), label = "fabAlpha"
                    )
                    val fabScale by animateFloatAsState(
                        targetValue = if (showScrollToBottom && messages.size > 1) 1f else 0.5f,
                        animationSpec = tween(200), label = "fabScale"
                    )
                    FloatingActionButton(
                        onClick = {
                            if (messages.isNotEmpty()) {
                                scope.launch {
                                    listState.animateScrollToItem(messages.size - 1)
                                }
                            }
                        },
                        modifier = Modifier
                            .align(Alignment.BottomCenter)
                            .size(36.dp)
                            .padding(bottom = 8.dp)
                            .alpha(fabAlpha)
                            .scale(fabScale),
                        containerColor = ElysiaPink.copy(alpha = 0.85f),
                        contentColor = TextOnBrand,
                        shape = CircleShape
                    ) {
                        Icon(
                            Icons.Default.KeyboardArrowDown,
                            "滚动到底部",
                            modifier = Modifier.size(20.dp)
                        )
                    }
                }
            }

            // ===== 输入栏 =====
            ChatInputBar(
                value = inputText,
                onValueChange = { inputText = it },
                onSend = {
                    if (inputText.isNotBlank()) {
                        viewModel.sendMessage(inputText)
                        inputText = ""
                    }
                },
                enabled = connectionState == ConnectionState.CONNECTED,
                isStreaming = isStreaming
            )
        }

        // ===== 离线遮罩 =====
        AnimatedVisibility(
            visible = connectionState == ConnectionState.DISCONNECTED && messages.size > 1,
            enter = fadeIn(tween(400)),
            exit = fadeOut(tween(300))
        ) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(OverlayDark),
                contentAlignment = Alignment.Center
            ) {
                Card(
                    colors = CardDefaults.cardColors(containerColor = ElysiaPurpleCard),
                    shape = RoundedCornerShape(CORNER_LARGE),
                    elevation = CardDefaults.cardElevation(defaultElevation = 12.dp)
                ) {
                    Column(
                        modifier = Modifier.padding(32.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        val iconScale by rememberInfiniteTransition().animateFloat(
                            initialValue = 1f, targetValue = 1.08f,
                            animationSpec = infiniteRepeatable(
                                animation = tween(800, easing = FastOutSlowInEasing),
                                repeatMode = RepeatMode.Reverse
                            ), label = "offlinePulse"
                        )
                        Box(
                            modifier = Modifier
                                .size(56.dp).scale(iconScale)
                                .clip(CircleShape)
                                .background(StatusOffline.copy(alpha = 0.15f)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(
                                Icons.Default.CloudOff, contentDescription = null,
                                modifier = Modifier.size(28.dp), tint = StatusOffline
                            )
                        }
                        Spacer(modifier = Modifier.height(16.dp))
                        Text(
                            "连接已断开",
                            style = MaterialTheme.typography.titleLarge,
                            color = MaterialTheme.colorScheme.onSurface,
                            fontWeight = FontWeight.Bold
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            "请检查服务器是否在运行",
                            style = MaterialTheme.typography.bodySmall,
                            color = TextSecondary
                        )
                        Spacer(modifier = Modifier.height(20.dp))
                        Button(
                            onClick = { viewModel.connect() },
                            colors = ButtonDefaults.buttonColors(containerColor = ElysiaPink),
                            shape = RoundedCornerShape(CORNER_MEDIUM),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Icon(Icons.Default.Refresh, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(modifier = Modifier.width(8.dp))
                            Text("重新连接", fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
            }
        }
    }
}

// ============================================================
// 日期分隔符 — 统一 12dp 圆角，轻量胶囊样式
// ============================================================

@Composable
private fun DateSeparator(text: String) {
    Box(
        modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
        contentAlignment = Alignment.Center
    ) {
        Surface(
            color = ElysiaPurpleCard.copy(alpha = 0.5f),
            shape = RoundedCornerShape(CORNER_MEDIUM)
        ) {
            Text(
                text = text,
                style = MaterialTheme.typography.labelSmall,
                color = TextSecondary.copy(alpha = 0.7f),
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 3.dp)
            )
        }
    }
}

// ============================================================
// 欢迎占位
// ============================================================

@Composable
private fun WelcomePlaceholder() {
    Box(
        modifier = Modifier.fillMaxWidth().padding(top = 50.dp),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            val glowAlpha by rememberInfiniteTransition().animateFloat(
                initialValue = 0.3f, targetValue = 0.6f,
                animationSpec = infiniteRepeatable(
                    animation = tween(2000, easing = FastOutSlowInEasing),
                    repeatMode = RepeatMode.Reverse
                ), label = "welcomeGlow"
            )
            Box(
                modifier = Modifier
                    .size(88.dp)
                    .shadow(24.dp, CircleShape)
                    .clip(CircleShape)
                    .background(
                        Brush.radialGradient(
                            colors = listOf(
                                ElysiaPinkGlow.copy(alpha = glowAlpha),
                                ElysiaPink.copy(alpha = 0.4f),
                                ElysiaPurple
                            )
                        )
                    ),
                contentAlignment = Alignment.Center
            ) {
                Text("💖", fontSize = 42.sp)
            }
            Spacer(modifier = Modifier.height(24.dp))
            Text(
                "你好，我是爱莉希雅",
                style = MaterialTheme.typography.headlineMedium,
                color = TextPrimary,
                fontWeight = FontWeight.Bold
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                "连接服务器后即可开始对话 ✨",
                style = MaterialTheme.typography.bodyMedium,
                color = TextSecondary
            )
            Spacer(modifier = Modifier.height(20.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                FeatureChip("💬", "智能对话")
                FeatureChip("🎯", "情感感知")
                FeatureChip("⚡", "实时响应")
            }
            Spacer(modifier = Modifier.height(16.dp))
            Surface(
                color = ElysiaPink.copy(alpha = 0.1f),
                shape = RoundedCornerShape(CORNER_MEDIUM)
            ) {
                Text(
                    text = "⚙️ 点击右上角设置按钮配置连接",
                    style = MaterialTheme.typography.bodySmall,
                    color = ElysiaPinkLight,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
                )
            }
        }
    }
}

@Composable
private fun FeatureChip(emoji: String, label: String) {
    Surface(
        color = ElysiaPurpleCard.copy(alpha = 0.6f),
        shape = RoundedCornerShape(CORNER_SMALL)
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(text = emoji, fontSize = 13.sp)
            Spacer(modifier = Modifier.width(6.dp))
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall,
                color = TextPrimary.copy(alpha = 0.8f)
            )
        }
    }
}

// ============================================================
// 消息气泡（优化版）
// - AI 连续消息仅首条显示头像
// - 技术参数（token/耗时）默认隐藏，长按菜单中可见
// - 时间戳统一右对齐，字体略大
// ============================================================

@Composable
fun MessageItem(
    message: ChatMessage,
    dateFormat: SimpleDateFormat,
    showAvatar: Boolean = true,
    onResend: () -> Unit = {},
    onRegenerate: () -> Unit = {}
) {
    val isUser = message.role == "user"
    val context = LocalContext.current
    var showContextMenu by remember { mutableStateOf(false) }

    // 消息入场动画
    var visible by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { visible = true }

    // 格式化文本 (支持 **粗体**)
    val formattedContent = remember(message.content) {
        buildAnnotatedString {
            val text = message.content
            var i = 0
            while (i < text.length) {
                if (text.startsWith("**", i)) {
                    val end = text.indexOf("**", i + 2)
                    if (end != -1 && end > i + 2) {
                        withStyle(SpanStyle(fontWeight = FontWeight.Bold, color = if (isUser) TextOnBrand else ElysiaPinkLight)) {
                            append(text.substring(i + 2, end))
                        }
                        i = end + 2
                        continue
                    }
                }
                val next = text.indexOf("**", i)
                if (next != -1) {
                    append(text.substring(i, next))
                    i = next
                } else {
                    append(text.substring(i))
                    break
                }
            }
        }
    }

    AnimatedVisibility(
        visible = visible,
        enter = slideInHorizontally(
            initialOffsetX = { if (isUser) 60 else -40 },
            animationSpec = tween(350, easing = FastOutSlowInEasing)
        ) + fadeIn(tween(300))
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp),
            horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
            verticalAlignment = Alignment.Bottom
        ) {
            // AI 头像 — 仅首条显示，后续用等宽空白占位保持对齐
            if (!isUser) {
                if (showAvatar) {
                    Box(
                        modifier = Modifier
                            .size(32.dp)
                            .clip(CircleShape)
                            .background(Brush.linearGradient(listOf(ElysiaPink, ElysiaPinkDark))),
                        contentAlignment = Alignment.Center
                    ) { Text("💖", fontSize = 15.sp) }
                } else {
                    // 占位空间保持气泡左对齐一致
                    Spacer(modifier = Modifier.size(32.dp))
                }
                Spacer(modifier = Modifier.width(8.dp))
            }

            // 气泡内容
            Column(
                modifier = Modifier
                    .widthIn(max = 280.dp)
                    .pointerInput(Unit) { detectTapGestures(onLongPress = { showContextMenu = true }) },
                horizontalAlignment = if (isUser) Alignment.End else Alignment.Start
            ) {
                Surface(
                    color = if (isUser) BubbleUser else BubbleAi,
                    shape = RoundedCornerShape(
                        topStart = if (isUser) CORNER_MEDIUM * 1.5f else CORNER_SMALL,
                        topEnd = if (isUser) CORNER_SMALL else CORNER_MEDIUM * 1.5f,
                        bottomStart = CORNER_MEDIUM,
                        bottomEnd = CORNER_MEDIUM
                    ),
                    shadowElevation = if (isUser) 2.dp else 0.5.dp,
                    tonalElevation = if (isUser) 2.dp else 0.dp,
                    modifier = if (isUser) Modifier.graphicsLayer {
                        shadowElevation = 6f
                    } else Modifier
                ) {
                    Column(modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
                        // 内容区域
                        if (message.isStreaming && message.content.isEmpty()) {
                            BlinkingCursor()
                        } else {
                            Text(
                                text = formattedContent,
                                color = if (isUser) TextOnBrand else MaterialTheme.colorScheme.onSurface,
                                style = MaterialTheme.typography.bodyMedium,
                                lineHeight = 22.sp
                            )
                            if (message.isStreaming) {
                                Spacer(modifier = Modifier.height(2.dp))
                                BlinkingCursor()
                            }
                        }

                        // 底栏：时间 + 状态（统一右对齐，字号增大到 11sp）
                        Spacer(modifier = Modifier.height(4.dp))
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.End,
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(
                                text = dateFormat.format(Date(message.timestamp)),
                                style = MaterialTheme.typography.labelSmall,
                                fontSize = 11.sp,
                                color = if (isUser) TextOnBrand.copy(alpha = 0.55f) else TextMuted.copy(alpha = 0.7f)
                            )

                            // 用户消息状态图标
                            if (isUser) {
                                Spacer(modifier = Modifier.width(3.dp))
                                Icon(
                                    imageVector = when (message.status) {
                                        MessageStatus.SENDING -> Icons.Default.Schedule
                                        MessageStatus.SENT -> Icons.Default.Done
                                        MessageStatus.DELIVERED -> Icons.Default.DoneAll
                                        MessageStatus.ERROR -> Icons.Default.ErrorOutline
                                    },
                                    contentDescription = null,
                                    modifier = Modifier.size(13.dp),
                                    tint = when (message.status) {
                                        MessageStatus.SENDING -> StatusConnecting
                                        MessageStatus.SENT -> TextOnBrand.copy(alpha = 0.55f)
                                        MessageStatus.DELIVERED -> StatusOnline
                                        MessageStatus.ERROR -> StatusOffline
                                    }
                                )
                            }
                        }
                    }
                }

                // ===== 长按菜单（技术参数仅在菜单中展示） =====
                DropdownMenu(
                    expanded = showContextMenu,
                    onDismissRequest = { showContextMenu = false }
                ) {
                    DropdownMenuItem(
                        text = { Text("📋 复制") },
                        onClick = { showContextMenu = false; copyToClipboard(context, message.content) },
                        leadingIcon = { Icon(Icons.Default.ContentCopy, null, modifier = Modifier.size(18.dp)) }
                    )
                    // 技术参数（仅 AI 消息且有数据时显示）
                    if (!isUser && message.tokens > 0) {
                        DropdownMenuItem(
                            text = { Text("ℹ️ ${message.tokens} tokens · ${message.responseMs}ms 响应耗时") },
                            onClick = { showContextMenu = false },
                            leadingIcon = { Icon(Icons.Default.Info, null, modifier = Modifier.size(18.dp)) },
                            enabled = false
                        )
                    }
                    if (isUser) {
                        DropdownMenuItem(
                            text = { Text("🔄 重新发送") },
                            onClick = { showContextMenu = false; onResend() },
                            leadingIcon = { Icon(Icons.Default.Refresh, null, modifier = Modifier.size(18.dp)) }
                        )
                    }
                    if (!isUser && !message.isStreaming) {
                        DropdownMenuItem(
                            text = { Text("✨ 重新生成") },
                            onClick = { showContextMenu = false; onRegenerate() },
                            leadingIcon = { Icon(Icons.Default.AutoAwesome, null, modifier = Modifier.size(18.dp)) }
                        )
                    }
                }
            }

            // 用户头像
            if (isUser) {
                Spacer(modifier = Modifier.width(8.dp))
                Box(
                    modifier = Modifier
                        .size(32.dp)
                        .clip(CircleShape)
                        .background(Brush.linearGradient(listOf(BubbleUser, BubbleUserGlow))),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(Icons.Default.Person, null, tint = TextOnBrand, modifier = Modifier.size(18.dp))
                }
            }
        }
    }
}

// ============================================================
// 闪动光标
// ============================================================

@Composable
private fun BlinkingCursor() {
    val alpha by rememberInfiniteTransition().animateFloat(
        initialValue = 0f, targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(500, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ), label = "cursorBlink"
    )
    Box(
        modifier = Modifier
            .width(2.dp).height(14.dp)
            .clip(RoundedCornerShape(1.dp))
            .background(ElysiaPink.copy(alpha = alpha))
    )
}

// ============================================================
// 剪贴板辅助
// ============================================================

fun copyToClipboard(context: Context, text: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText("elysia_message", text))
    Toast.makeText(context, "已复制到剪贴板", Toast.LENGTH_SHORT).show()
}

// ============================================================
// 输入中指示器
// ============================================================

@Composable
fun TypingIndicator() {
    Row(
        modifier = Modifier.padding(start = 44.dp, top = 6.dp, bottom = 2.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .size(20.dp)
                .clip(CircleShape)
                .background(Brush.linearGradient(listOf(ElysiaPink, ElysiaPinkDark))),
            contentAlignment = Alignment.Center
        ) { Text("💖", fontSize = 10.sp) }
        Text(
            "爱莉正在输入",
            color = ElysiaPink.copy(alpha = 0.7f),
            style = MaterialTheme.typography.labelSmall
        )
        repeat(3) { index ->
            val dotAlpha by rememberInfiniteTransition().animateFloat(
                initialValue = 0.2f, targetValue = 1f,
                animationSpec = infiniteRepeatable(
                    animation = tween(400, delayMillis = index * 150, easing = FastOutSlowInEasing),
                    repeatMode = RepeatMode.Reverse
                ), label = "dot$index"
            )
            Box(
                modifier = Modifier
                    .size(4.dp)
                    .clip(CircleShape)
                    .alpha(dotAlpha)
                    .background(ElysiaPink)
            )
        }
    }
}

// ============================================================
// 聊天输入栏 — 优化版
// - "+" 展开菜单（快捷功能入口）
// - 占位文字对比度提升
// - 发送按钮改为圆角矩形统一风格
// ============================================================

@Composable
fun ChatInputBar(
    value: String,
    onValueChange: (String) -> Unit,
    onSend: () -> Unit,
    enabled: Boolean,
    isStreaming: Boolean = false
) {
    val characterLimit = 2000
    val charCount = value.length
    val isOverLimit = charCount > characterLimit
    var showQuickActions by remember { mutableStateOf(false) }

    Surface(
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.95f),
        shadowElevation = 12.dp,
        tonalElevation = 4.dp
    ) {
        Column {
            // 字数提示 (当接近限制时)
            AnimatedVisibility(
                visible = charCount > characterLimit * 0.7f,
                enter = expandVertically() + fadeIn(),
                exit = shrinkVertically() + fadeOut()
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 2.dp),
                    horizontalArrangement = Arrangement.End
                ) {
                    Text(
                        text = "$charCount/$characterLimit",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (isOverLimit) StatusOffline else TextMuted
                    )
                }
            }

            // 快捷功能菜单（+ 展开）
            AnimatedVisibility(
                visible = showQuickActions,
                enter = expandVertically(expandFrom = Alignment.Bottom) + fadeIn(tween(200)),
                exit = shrinkVertically(shrinkTowards = Alignment.Bottom) + fadeOut(tween(150))
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp, vertical = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    QuickActionChip(
                        icon = Icons.Default.Image,
                        label = "图片",
                        onClick = { /* TODO: 后续实现 */ showQuickActions = false }
                    )
                    QuickActionChip(
                        icon = Icons.Default.KeyboardVoice,
                        label = "语音",
                        onClick = { /* TODO: 后续实现 */ showQuickActions = false }
                    )
                    QuickActionChip(
                        icon = Icons.Default.EmojiEmotions,
                        label = "常用语",
                        onClick = { /* TODO: 后续实现 */ showQuickActions = false }
                    )
                }
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 8.dp)
                    .navigationBarsPadding()
                    .imePadding(),
                verticalAlignment = Alignment.Bottom,
                horizontalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                // "+" 展开按钮
                IconButton(
                    onClick = { showQuickActions = !showQuickActions },
                    modifier = Modifier.size(40.dp)
                ) {
                    Icon(
                        Icons.Default.Add,
                        contentDescription = "更多功能",
                        tint = if (showQuickActions) ElysiaPink else TextSecondary,
                        modifier = Modifier.size(22.dp)
                    )
                }

                // 输入框
                OutlinedTextField(
                    value = value,
                    onValueChange = { if (it.length <= characterLimit + 50) onValueChange(it) },
                    modifier = Modifier.weight(1f),
                    placeholder = {
                        Text(
                            text = when {
                                !enabled -> "等待连接..."
                                isStreaming -> "爱莉正在回复..."
                                else -> "说点什么..."
                            },
                            color = TextSecondary.copy(alpha = 0.55f),
                            style = MaterialTheme.typography.bodyMedium
                        )
                    },
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = MaterialTheme.colorScheme.onSurface,
                        unfocusedTextColor = MaterialTheme.colorScheme.onSurface,
                        disabledTextColor = TextSecondary,
                        focusedContainerColor = ElysiaSurfaceVariant,
                        unfocusedContainerColor = ElysiaSurfaceVariant,
                        disabledContainerColor = ElysiaSurfaceVariant.copy(alpha = 0.5f),
                        focusedBorderColor = ElysiaPink.copy(alpha = 0.5f),
                        unfocusedBorderColor = MaterialTheme.colorScheme.outlineVariant,
                        cursorColor = ElysiaPink,
                    ),
                    shape = RoundedCornerShape(CORNER_LARGE),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { if (enabled && value.isNotBlank() && !isStreaming) onSend() }),
                    maxLines = 4,
                    minLines = 1,
                    enabled = enabled
                )

                // 发送按钮 — 圆角矩形，与输入框风格统一
                val sendScale by animateFloatAsState(
                    targetValue = if (value.isNotBlank() && enabled && !isStreaming) 1f else 0.9f,
                    animationSpec = tween(200), label = "sendScale"
                )
                if (value.isNotBlank() && enabled && !isStreaming) {
                    Button(
                        onClick = onSend,
                        enabled = enabled && value.isNotBlank() && !isStreaming,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = ElysiaPink,
                            contentColor = TextOnBrand,
                            disabledContainerColor = ElysiaPink.copy(alpha = 0.2f),
                            disabledContentColor = TextOnBrand.copy(alpha = 0.35f)
                        ),
                        shape = RoundedCornerShape(CORNER_LARGE),
                        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 0.dp),
                        modifier = Modifier
                            .height(46.dp)
                            .scale(sendScale)
                    ) {
                        Icon(Icons.Filled.Send, "发送", modifier = Modifier.size(18.dp))
                        Spacer(modifier = Modifier.width(4.dp))
                        Text("发送", fontWeight = FontWeight.Medium, fontSize = 14.sp)
                    }
                } else {
                    FilledIconButton(
                        onClick = onSend,
                        enabled = false,
                        colors = IconButtonDefaults.filledIconButtonColors(
                            containerColor = ElysiaPink.copy(alpha = 0.15f),
                            contentColor = TextOnBrand.copy(alpha = 0.3f),
                            disabledContainerColor = ElysiaPink.copy(alpha = 0.15f),
                            disabledContentColor = TextOnBrand.copy(alpha = 0.3f)
                        ),
                        modifier = Modifier
                            .size(42.dp)
                            .align(Alignment.CenterVertically),
                        shape = RoundedCornerShape(CORNER_LARGE)
                    ) {
                        Icon(Icons.Filled.Send, "发送", modifier = Modifier.size(18.dp))
                    }
                }
            }
        }
    }
}

// ============================================================
// 快捷功能按钮组件
// ============================================================

@Composable
private fun QuickActionChip(
    icon: ImageVector,
    label: String,
    onClick: () -> Unit
) {
    Surface(
        onClick = onClick,
        color = ElysiaPink.copy(alpha = 0.08f),
        shape = RoundedCornerShape(CORNER_SMALL)
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Icon(
                icon,
                contentDescription = label,
                tint = ElysiaPinkLight,
                modifier = Modifier.size(22.dp)
            )
            Spacer(modifier = Modifier.height(2.dp))
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall,
                color = ElysiaPinkLight,
                fontSize = 10.sp
            )
        }
    }
}