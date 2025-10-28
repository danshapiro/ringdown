package com.ringdown.mobile.domain

import java.time.Instant

data class ManagedVoiceSession(
    val sessionId: String,
    val agent: String,
    val roomUrl: String,
    val accessToken: String,
    val expiresAt: Instant,
    val pipelineSessionId: String?,
    val metadata: Map<String, Any?>,
    val greeting: String?,
)
