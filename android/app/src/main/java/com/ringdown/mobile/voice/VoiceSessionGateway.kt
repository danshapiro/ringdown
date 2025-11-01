package com.ringdown.mobile.voice

import android.media.projection.MediaProjection
import kotlinx.coroutines.flow.StateFlow

interface VoiceSessionGateway {
    val state: StateFlow<VoiceConnectionState>
    fun start(deviceId: String, agent: String?)
    fun stop()
    fun updateMediaProjection(token: MediaProjection?)
}
