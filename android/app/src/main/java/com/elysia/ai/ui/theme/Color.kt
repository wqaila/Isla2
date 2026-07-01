package com.elysia.ai.ui.theme

import androidx.compose.ui.graphics.Color

// =============================================
// 爱莉希雅主题色系 — Elysia Theme Colors
// 灵感来源：粉色系 + 深夜紫 + 流体渐变
// =============================================

// --- 主色：粉色系 (Pink Family) ---
val ElysiaPink = Color(0xFFFF69B4)          // 亮粉 - 主色
val ElysiaPinkDark = Color(0xFFE91E8C)       // 深粉 - 强调
val ElysiaPinkLight = Color(0xFFFFB6C1)      // 浅粉 - 装饰
val ElysiaPinkGlow = Color(0xFFFF85C0)       // 辉光粉 - 高亮动画用
val ElysiaPinkMuted = Color(0x33FF69B4)      // 半透明粉 - 背景/卡片

// --- 辅色：紫色系 (Purple Family) ---
val ElysiaPurple = Color(0xFF9B59B6)         // 雅紫
val ElysiaPurpleDeep = Color(0xFF1A0A2E)     // 深紫黑 - 背景最深层
val ElysiaPurpleSurface = Color(0xFF1E1038)  // 紫黑 - 卡片/表面
val ElysiaPurpleCard = Color(0xFF261845)     // 浅紫黑 - 次级卡片

// --- 强调色 (Accent) ---
val ElysiaGold = Color(0xFFFFD700)           // 金 - 重要提示
val ElysiaCyan = Color(0xFF00D4D4)           // 青 - 科技感/链接
val ElysiaLavender = Color(0xFFB388FF)       // 薰衣草 - 柔和强调
val ElysiaPurpleLight = ElysiaLavender       // 别名 - 淡紫

// --- 背景层级 (Background Hierarchy) ---
val ElysiaBackground = Color(0xFF0A0A1A)     // 最深背景 — 整个屏幕底色
val ElysiaSurface = Color(0xFF12122A)        // 第一级表面 — 卡片/TopBar
val ElysiaSurfaceLight = Color(0xFF2D1B4E)   // 保留兼容
val ElysiaSurfaceVariant = Color(0xFF1A1A3E) // 第二级表面 — 输入框/气泡底

// --- 文字色 (Text) ---
val TextPrimary = Color(0xFFF0E6FF)          // 主文字 - 带淡紫的白色
val TextSecondary = Color(0xFF8E8EAA)        // 次要文字 - 灰紫
val TextMuted = Color(0xFF5A5A72)            // 禁用/提示文字
val TextOnBrand = Color(0xFFFFFFFF)          // 品牌色上的文字 (纯白)
val TextOnDark = Color(0xFFFFFFFF)

// --- 功能色 (Status / Semantic) ---
val StatusOnline = Color(0xFF3DDC84)         // 在线 - Android 绿
val StatusOffline = Color(0xFFFF5252)        // 离线 - 暖红
val StatusConnecting = Color(0xFFFFB74D)     // 连接中 - 暖橙
val StatusWarning = Color(0xFFFFD54F)        // 警告 - 琥珀

// --- 消息气泡 (Chat Bubbles) ---
val UserBubble = Color(0xFF4F5DE0)           // 用户气泡 - 靛蓝 (与粉色互补)
val UserBubbleBorder = Color(0xFF6371F2)     // 用户气泡边框
val BubbleUser = Color(0xFF4F5DE0)           // 别名
val BubbleUserGlow = Color(0xFF6371F2)       // 用户气泡辉光
val BubbleAi = Color(0xFF261845)             // AI 气泡底色 - 深紫
val BubbleAiBorder = Color(0x33FF69B4)       // AI 气泡边框 - 粉色透明
val AssistantBubble = Color(0xFFFF69B4).copy(alpha = 0.15f)   // 保留兼容
val AssistantBubbleBorder = Color(0xFFFF69B4).copy(alpha = 0.3f)

// --- 渐变色端点 (Gradient endpoints) ---
val GradientPinkStart = Color(0xFFFF69B4)
val GradientPinkEnd = Color(0xFFE040FB)      // 粉→紫渐变
val GradientPurpleStart = Color(0xFF3D1E6D)
val GradientPurpleEnd = Color(0xFF1A0A3E)

// --- 阴影/遮罩 ---
val OverlayDark = Color(0x80000000)          // 半透明黑色遮罩
val OverlayGlow = Color(0x1AFF69B4)          // 粉色辉光遮罩
