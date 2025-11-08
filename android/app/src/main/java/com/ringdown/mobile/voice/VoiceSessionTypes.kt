package com.ringdown.mobile.voice

import java.time.Instant

fun interface InstantProvider {
    fun now(): Instant
}

sealed class VoiceConnectionState {
    object Idle : VoiceConnectionState()
    object Connecting : VoiceConnectionState()
    data class Connected(val transcripts: List<TranscriptMessage>) : VoiceConnectionState()
    data class Failed(val reason: String) : VoiceConnectionState()
}

data class TranscriptMessage(
    val speaker: String,
    val text: String,
    val timestampIso: String?,
    val messageType: String? = null,
    val toolPayload: Map<String, Any?>? = null,
)
