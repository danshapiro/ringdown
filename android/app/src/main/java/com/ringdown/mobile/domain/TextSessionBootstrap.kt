package com.ringdown.mobile.domain

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
)
