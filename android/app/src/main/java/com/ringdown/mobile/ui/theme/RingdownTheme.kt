package com.ringdown.mobile.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val LightColors = lightColorScheme(
    primary = Color(0xFF2E5AAC),
    onPrimary = Color.White,
    secondary = Color(0xFF4F5B62),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF9BB4FF),
    onPrimary = Color.Black,
    secondary = Color(0xFFB0BEC5),
)

@Composable
fun RingdownTheme(
    useDarkTheme: Boolean,
    content: @Composable () -> Unit,
) {
    val colors = if (useDarkTheme) DarkColors else LightColors
    MaterialTheme(
        colorScheme = colors,
        typography = MaterialTheme.typography,
        content = content,
    )
}
