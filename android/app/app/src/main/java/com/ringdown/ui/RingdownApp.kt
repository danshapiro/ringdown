package com.ringdown.ui

import android.Manifest
import android.os.Build
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.google.accompanist.permissions.ExperimentalPermissionsApi
import com.google.accompanist.permissions.rememberMultiplePermissionsState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.DevicesOther
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.ui.text.style.TextOverflow

@Composable
fun RingdownAppRoot(
    viewModel: MainViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    when (val current = state) {
        AppViewState.Loading -> LoadingScreen()
        is AppViewState.Idle -> IdleScreen(
            statusMessage = current.statusMessage,
            onReconnect = viewModel::onReconnectRequested,
            onOpenChat = { /* Phase 5 */ },
            onPermissionDenied = viewModel::onPermissionDenied
        )
        is AppViewState.PendingApproval -> PendingApprovalScreen(
            deviceId = current.deviceId,
            attempts = current.attempts,
            nextPollInSeconds = current.nextPollInSeconds,
            onCheckAgain = viewModel::onCheckAgain
        )
        is AppViewState.Error -> ErrorScreen(
            message = current.message,
            onRetry = viewModel::onCheckAgain
        )
    }
}

@Composable
private fun LoadingScreen() {
    IdleBackground {
        CircularProgressIndicator(
            color = Color.White,
            strokeWidth = 4.dp
        )
    }
}

@OptIn(ExperimentalPermissionsApi::class)
@Composable
private fun IdleScreen(
    statusMessage: String?,
    onReconnect: () -> Unit,
    onOpenChat: () -> Unit,
    onPermissionDenied: () -> Unit
) {
    val permissionList = remember {
        buildList {
            add(Manifest.permission.RECORD_AUDIO)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                add(Manifest.permission.BLUETOOTH_CONNECT)
            }
        }
    }

    val permissionsState = rememberMultiplePermissionsState(permissionList)
    var awaitingPermissionResult by rememberSaveable { mutableStateOf(false) }
    var lastSnapshot by remember { mutableStateOf(permissionsState.permissions.map { it.status }) }

    LaunchedEffect(permissionsState.permissions, awaitingPermissionResult) {
        val currentSnapshot = permissionsState.permissions.map { it.status }
        if (!awaitingPermissionResult) {
            lastSnapshot = currentSnapshot
            return@LaunchedEffect
        }
        if (currentSnapshot == lastSnapshot) {
            return@LaunchedEffect
        }
        lastSnapshot = currentSnapshot
        awaitingPermissionResult = false

        if (permissionsState.allPermissionsGranted) {
            onReconnect()
        } else {
            onPermissionDenied()
        }
    }

    IdleBackground {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 32.dp),
            verticalArrangement = Arrangement.spacedBy(24.dp, Alignment.CenterVertically),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                text = "RINGDOWN",
                style = MaterialTheme.typography.labelSmall.copy(
                    color = Color.White.copy(alpha = 0.75f),
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 3.sp
                )
            )

            GradientButton(
                text = "Reconnect",
                modifier = Modifier.fillMaxWidth(),
                onClick = {
                    if (permissionsState.allPermissionsGranted) {
                        onReconnect()
                    } else {
                        awaitingPermissionResult = true
                        permissionsState.launchMultiplePermissionRequest()
                    }
                }
            )

            if (!statusMessage.isNullOrBlank()) {
                Text(
                    text = statusMessage,
                    style = MaterialTheme.typography.bodyMedium.copy(
                        color = Color.White.copy(alpha = 0.75f),
                        textAlign = TextAlign.Center
                    ),
                    modifier = Modifier.fillMaxWidth()
                )
            }

            SecondaryButton(
                text = "Open Chat",
                modifier = Modifier.fillMaxWidth(),
                onClick = onOpenChat
            )
        }
    }
}

@Composable
private fun PendingApprovalScreen(
    deviceId: String,
    attempts: Int,
    nextPollInSeconds: Long?,
    onCheckAgain: () -> Unit
) {
    PendingBackground {
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp),
            color = Color(0xF0191E30),
            shape = RoundedCornerShape(24.dp),
            tonalElevation = 0.dp,
            shadowElevation = 24.dp
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 48.dp, horizontal = 32.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(24.dp)
            ) {
                PulseIcon()

                Text(
                    text = "Approval Required",
                    textAlign = TextAlign.Center,
                    style = MaterialTheme.typography.headlineSmall.copy(
                        color = Color.White,
                        fontWeight = FontWeight.SemiBold
                    )
                )

                Text(
                    text = "Hang tight while an administrator approves this device. You can tap below to retry once they flip it on.",
                    style = MaterialTheme.typography.bodyMedium.copy(
                        color = Color.White.copy(alpha = 0.75f),
                        textAlign = TextAlign.Center,
                        lineHeight = 20.sp
                    )
                )

                Column(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Text(
                        text = "Device ID",
                        style = MaterialTheme.typography.labelSmall.copy(
                            color = Color.White.copy(alpha = 0.6f),
                            fontWeight = FontWeight.Medium,
                            letterSpacing = 1.5.sp
                        )
                    )
                    Surface(
                        modifier = Modifier.fillMaxWidth(),
                        color = Color.Black.copy(alpha = 0.35f),
                        shape = RoundedCornerShape(12.dp),
                        border = androidx.compose.foundation.BorderStroke(
                            width = 1.dp,
                            color = Color.White.copy(alpha = 0.2f)
                        )
                    ) {
                        Text(
                            text = deviceId.ifBlank { "UNKNOWN" },
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = 14.dp),
                            style = MaterialTheme.typography.titleMedium.copy(
                                color = Color(0xFF7B61FF),
                                fontFamily = MaterialTheme.typography.titleMedium.fontFamily,
                                letterSpacing = 2.sp
                            ),
                            textAlign = TextAlign.Center,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }

                val attemptsLabel = "Attempts: $attempts"
                Text(
                    text = buildString {
                        append(attemptsLabel)
                        if (nextPollInSeconds != null && nextPollInSeconds > 0) {
                            append(" • Next auto-check in ${nextPollInSeconds}s")
                        }
                    },
                    style = MaterialTheme.typography.bodySmall.copy(
                        color = Color.White.copy(alpha = 0.65f)
                    )
                )

                GradientButton(
                    text = "Check again",
                    modifier = Modifier.fillMaxWidth(),
                    onClick = onCheckAgain,
                    leadingIcon = Icons.Outlined.Refresh
                )
            }
        }
    }
}

@Composable
private fun ErrorScreen(
    message: String,
    onRetry: () -> Unit
) {
    IdleBackground {
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 32.dp),
            color = Color.Black.copy(alpha = 0.35f),
            shape = RoundedCornerShape(24.dp)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 32.dp, vertical = 40.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(24.dp)
            ) {
                Text(
                    text = message,
                    textAlign = TextAlign.Center,
                    style = MaterialTheme.typography.bodyLarge.copy(
                        color = Color.White,
                        lineHeight = 22.sp
                    )
                )
                GradientButton(
                    text = "Retry",
                    modifier = Modifier.fillMaxWidth(),
                    onClick = onRetry
                )
            }
        }
    }
}

@Composable
private fun IdleBackground(
    content: @Composable BoxScope.() -> Unit
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                brush = Brush.linearGradient(
                    listOf(
                        Color(0xFF0F1729),
                        Color(0xFF1A1F3A),
                        Color(0xFF0A0E27)
                    )
                )
            )
    ) {
        AnimatedGrid(
            modifier = Modifier
                .fillMaxSize()
                .align(Alignment.Center)
        )
        FloatingOrb(
            baseOffsetX = 40.dp,
            baseOffsetY = 80.dp,
            travelX = 32f,
            travelY = -28f,
            size = 260.dp,
            color = Color(0xFF7B61FF),
            durationMillis = 16000,
            delayMillis = 0
        )
        FloatingOrb(
            baseOffsetX = 220.dp,
            baseOffsetY = 420.dp,
            travelX = -24f,
            travelY = 24f,
            size = 220.dp,
            color = Color(0xFF5A3FD9),
            durationMillis = 18000,
            delayMillis = 3000
        )
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 24.dp),
            contentAlignment = Alignment.Center
        ) {
            content()
        }
    }
}

@Composable
private fun PendingBackground(
    content: @Composable BoxScope.() -> Unit
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                brush = Brush.linearGradient(
                    listOf(
                        Color(0xFF0A0E27),
                        Color(0xFF1A1F3A)
                    )
                )
            )
    ) {
        Particle(
            size = 80.dp,
            baseOffsetX = 48.dp,
            baseOffsetY = 120.dp,
            travelX = 24f,
            travelY = -24f,
            durationMillis = 20000,
            delayMillis = 0
        )
        Particle(
            size = 120.dp,
            baseOffsetX = 260.dp,
            baseOffsetY = 420.dp,
            travelX = -28f,
            travelY = 18f,
            durationMillis = 22000,
            delayMillis = 4000
        )
        Particle(
            size = 60.dp,
            baseOffsetX = 90.dp,
            baseOffsetY = 520.dp,
            travelX = -16f,
            travelY = 22f,
            durationMillis = 19000,
            delayMillis = 2000
        )
        Particle(
            size = 100.dp,
            baseOffsetX = 220.dp,
            baseOffsetY = 220.dp,
            travelX = 18f,
            travelY = -18f,
            durationMillis = 21000,
            delayMillis = 6000
        )
        Box(
            modifier = Modifier.fillMaxSize(),
            contentAlignment = Alignment.Center
        ) {
            content()
        }
    }
}

@Composable
private fun BoxScope.AnimatedGrid(modifier: Modifier = Modifier) {
    val transition = rememberInfiniteTransition(label = "grid")
    val progress by transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = androidx.compose.animation.core.tween(
                durationMillis = 20000,
                easing = LinearEasing
            )
        ),
        label = "grid-offset"
    )

    Canvas(
        modifier = modifier
            .matchParentSize()
            .align(Alignment.Center)
    ) {
        val step = 50.dp.toPx()
        val offset = progress * step

        val color = Color(0xFF7B61FF).copy(alpha = 0.05f)

        var x = -step + offset
        while (x < size.width + step) {
            drawLine(
                color = color,
                start = Offset(x, 0f),
                end = Offset(x, size.height),
                strokeWidth = 1f
            )
            x += step
        }

        var y = -step + offset
        while (y < size.height + step) {
            drawLine(
                color = color,
                start = Offset(0f, y),
                end = Offset(size.width, y),
                strokeWidth = 1f
            )
            y += step
        }
    }
}

@Composable
private fun BoxScope.FloatingOrb(
    baseOffsetX: Dp,
    baseOffsetY: Dp,
    travelX: Float,
    travelY: Float,
    size: Dp,
    color: Color,
    durationMillis: Int,
    delayMillis: Int
) {
    val transition = rememberInfiniteTransition(label = "orb-transition")
    val progress by transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = androidx.compose.animation.core.tween(
                durationMillis = durationMillis,
                easing = LinearEasing,
                delayMillis = delayMillis
            ),
            repeatMode = RepeatMode.Reverse
        ),
        label = "orb-progress"
    )

    val offsetX = baseOffsetX + (travelX * progress).dp
    val offsetY = baseOffsetY + (travelY * progress).dp

    Box(
        modifier = Modifier
            .align(Alignment.TopStart)
            .offset(x = offsetX, y = offsetY)
            .size(size)
            .blur(140.dp)
            .background(color.copy(alpha = 0.35f), CircleShape)
    )
}

@Composable
private fun BoxScope.Particle(
    size: Dp,
    baseOffsetX: Dp,
    baseOffsetY: Dp,
    travelX: Float,
    travelY: Float,
    durationMillis: Int,
    delayMillis: Int
) {
    val transition = rememberInfiniteTransition(label = "particle-transition")
    val progress by transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = androidx.compose.animation.core.tween(
                durationMillis = durationMillis,
                easing = LinearEasing,
                delayMillis = delayMillis
            ),
            repeatMode = RepeatMode.Reverse
        ),
        label = "particle-progress"
    )

    val offsetX = baseOffsetX + (travelX * progress).dp
    val offsetY = baseOffsetY + (travelY * progress).dp

    Box(
        modifier = Modifier
            .align(Alignment.TopStart)
            .offset(x = offsetX, y = offsetY)
            .size(size)
            .blur(60.dp)
            .background(Color(0xFF7B61FF).copy(alpha = 0.12f), CircleShape)
    )
}

@Composable
private fun GradientButton(
    text: String,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
    leadingIcon: androidx.compose.ui.graphics.vector.ImageVector? = null
) {
    val shape = RoundedCornerShape(32.dp)
    Box(
        modifier = modifier
            .height(64.dp)
            .shadow(
                elevation = 24.dp,
                shape = shape,
                clip = false
            )
            .clip(shape)
            .background(
                brush = Brush.linearGradient(
                    listOf(
                        Color(0xFF7B61FF),
                        Color(0xFF5A3FD9)
                    )
                )
            )
            .border(
                width = 1.dp,
                color = Color(0xFF7B61FF).copy(alpha = 0.35f),
                shape = shape
            )
            .clickable(role = Role.Button, onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (leadingIcon != null) {
                Icon(
                    imageVector = leadingIcon,
                    contentDescription = null,
                    tint = Color.White,
                    modifier = Modifier.padding(end = 12.dp)
                )
            }
            Text(
                text = text,
                style = MaterialTheme.typography.titleMedium.copy(
                    color = Color.White,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = 20.sp
                )
            )
        }
    }
}

@Composable
private fun SecondaryButton(
    text: String,
    modifier: Modifier = Modifier,
    onClick: () -> Unit
) {
    val shape = RoundedCornerShape(32.dp)
    Box(
        modifier = modifier
            .height(64.dp)
            .clip(shape)
            .border(
                width = 1.5.dp,
                color = Color.White.copy(alpha = 0.35f),
                shape = shape
            )
            .background(Color.White.copy(alpha = 0.06f))
            .clickable(role = Role.Button, onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.titleMedium.copy(
                color = Color.White,
                fontWeight = FontWeight.Medium,
                fontSize = 18.sp
            )
        )
    }
}

@Composable
private fun PulseIcon() {
    val transition = rememberInfiniteTransition(label = "pulse")
    val scale by transition.animateFloat(
        initialValue = 1f,
        targetValue = 1.08f,
        animationSpec = infiniteRepeatable(
            animation = androidx.compose.animation.core.tween(
                durationMillis = 1800,
                easing = LinearEasing
            ),
            repeatMode = RepeatMode.Reverse
        ),
        label = "pulse-scale"
    )

    Box(
        modifier = Modifier
            .size(96.dp)
            .shadow(16.dp, CircleShape, clip = false)
            .clip(CircleShape)
            .background(Color(0x337B61FF))
            .border(2.dp, Color(0xFF7B61FF).copy(alpha = 0.4f), CircleShape),
        contentAlignment = Alignment.Center
    ) {
        Icon(
            imageVector = Icons.Outlined.DevicesOther,
            contentDescription = null,
            tint = Color(0xFF7B61FF),
            modifier = Modifier
                .size(40.dp * scale)
        )
    }
}
