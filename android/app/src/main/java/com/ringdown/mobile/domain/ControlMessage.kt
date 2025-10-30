package com.ringdown.mobile.domain

data class ControlMessage(
    val messageId: String,
    val promptId: String,
    val audioBase64: String,
    val sampleRateHz: Int,
    val channels: Int,
    val format: String,
    val metadata: Map<String, Any?>,
    val enqueuedAtIso: String?,
)
