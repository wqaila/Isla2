package com.elysia.ai.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

// ===== 爱莉希雅暗色主题 — 深紫+粉色梦幻配色 =====
private val DarkColorScheme = darkColorScheme(
    primary = ElysiaPink,
    onPrimary = Color.White,
    primaryContainer = ElysiaPurpleCard,
    onPrimaryContainer = ElysiaPinkLight,
    secondary = ElysiaCyan,
    onSecondary = Color.Black,
    secondaryContainer = Color(0xFF003740),
    onSecondaryContainer = ElysiaCyan,
    tertiary = ElysiaGold,
    onTertiary = Color.Black,
    tertiaryContainer = Color(0xFF4A3800),
    onTertiaryContainer = ElysiaGold,
    background = ElysiaPurpleDeep,
    onBackground = TextPrimary,
    surface = ElysiaPurpleSurface,
    onSurface = TextPrimary,
    surfaceVariant = ElysiaPurpleCard,
    onSurfaceVariant = TextSecondary,
    outline = TextSecondary.copy(alpha = 0.3f),
    outlineVariant = TextSecondary.copy(alpha = 0.12f),
    error = StatusOffline,
    onError = Color.White,
    errorContainer = StatusOffline.copy(alpha = 0.2f),
    onErrorContainer = StatusOffline,
    inverseSurface = Color(0xFFF0E6FF),
    inverseOnSurface = ElysiaPurpleDeep,
    inversePrimary = ElysiaPinkDark,
)

// ===== 浅色主题 — 柔和粉白配色 =====
private val LightColorScheme = lightColorScheme(
    primary = ElysiaPinkDark,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFFFD6E7),
    onPrimaryContainer = Color(0xFF3D0030),
    secondary = ElysiaCyan,
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFCEFAFF),
    onSecondaryContainer = Color(0xFF003740),
    tertiary = ElysiaGold,
    onTertiary = Color.Black,
    tertiaryContainer = Color(0xFFFFF0B3),
    onTertiaryContainer = Color(0xFF4A3800),
    background = Color(0xFFFDF7FA),
    onBackground = Color(0xFF1C1B1F),
    surface = Color.White,
    onSurface = Color(0xFF1C1B1F),
    surfaceVariant = Color(0xFFF5E6EB),
    onSurfaceVariant = Color(0xFF4A4458),
    outline = Color(0xFF79747E),
    outlineVariant = Color(0xFFCAC4D0),
    error = Color(0xFFD32F2F),
    onError = Color.White,
    errorContainer = Color(0xFFFFDAD6),
    onErrorContainer = Color(0xFF410002),
    inverseSurface = ElysiaPurpleDeep,
    inverseOnSurface = TextPrimary,
    inversePrimary = ElysiaPink,
)

@Composable
fun ElysiaAITheme(
    darkTheme: Boolean = true,
    content: @Composable () -> Unit
) {
    val colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme

    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = colorScheme.surface.toArgb()
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = ElysiaTypography,
        content = content
    )
}
