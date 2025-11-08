package com.ringdown.mobile.domain

import com.ringdown.mobile.chat.ChatMessage

data class TextSessionBootstrap(
    val sessionId: String,
    val sessionToken: String,
    val resumeToken: String?,
    val websocketPath: String,
    val agent: String,
    val expiresAtIso: String,
    val heartbeatIntervalSeconds: Int,
    val heartbeatTimeoutSeconds: Int,
    val tlsPins: List<String>,
    val history: List<ChatMessage> = emptyList(),
)
