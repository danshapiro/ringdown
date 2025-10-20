package com.ringdown.data.voice

import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asSharedFlow

@Singleton
class FakeVoiceTransport @Inject constructor() : VoiceTransport {

    private val incomingFrames = MutableSharedFlow<VoiceTransport.AudioFrame>(
        replay = 0,
        extraBufferCapacity = 1
    )
    private var connected = false

    override suspend fun connect(parameters: VoiceTransport.ConnectParameters) {
        delay(250)
        connected = true
    }

    override suspend fun sendAudioFrame(frame: VoiceTransport.AudioFrame) {
        if (!connected) return
        incomingFrames.tryEmit(frame)
    }

    override fun receiveAudioFrames(): Flow<VoiceTransport.AudioFrame> = incomingFrames.asSharedFlow()

    override suspend fun teardown() {
        connected = false
    }
}
