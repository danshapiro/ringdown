package com.ringdown.mobile.text

/**
 * Represents a single assistant token frame received over the mobile text session.
 *
 * Captures sufficient metadata for instrumentation, logging, and automated validation.
 */
data class AssistantTokenTrace(
    val token: String,
    val final: Boolean,
    val messageType: String?,
    val sessionId: String?,
    val receivedAtIso: String,
)
