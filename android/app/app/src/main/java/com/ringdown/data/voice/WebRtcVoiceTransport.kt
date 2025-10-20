package com.ringdown.data.voice

import android.util.Log
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow

@Singleton
class WebRtcVoiceTransport @Inject constructor() : VoiceTransport {

    override suspend fun connect(parameters: VoiceTransport.ConnectParameters) {
        Log.w(TAG, "WebRTC transport is not yet wired to backend signaling. Device=${parameters.deviceId}")
        throw UnsupportedOperationException("WebRTC voice transport not implemented yet")
    }

    override suspend fun sendAudioFrame(frame: VoiceTransport.AudioFrame) {
        Log.d(TAG, "Ignoring audio frame send; transport not active")
    }

    override fun receiveAudioFrames(): Flow<VoiceTransport.AudioFrame> = emptyFlow()

    override suspend fun teardown() {
        Log.d(TAG, "Transport teardown invoked without active connection")
    }

    private companion object {
        const val TAG = "WebRtcVoiceTransport"
    }
}
