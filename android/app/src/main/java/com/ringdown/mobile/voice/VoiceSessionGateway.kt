package com.ringdown.mobile.voice

import kotlinx.coroutines.flow.StateFlow

interface VoiceSessionGateway {
    val state: StateFlow<VoiceConnectionState>
    fun start(deviceId: String, agent: String?)
    fun stop()
}
