package com.ringdown.domain.model

import java.time.Instant

sealed interface VoiceSessionState {
    data object Disconnected : VoiceSessionState
    data object Connecting : VoiceSessionState
    data class Active(val connectedSince: Instant) : VoiceSessionState
    data class Reconnecting(val attempt: Int) : VoiceSessionState
    data class Error(val message: String) : VoiceSessionState
}

data class VoiceSessionTelemetry(
    val deviceId: String,
    val backendUrl: String
)
