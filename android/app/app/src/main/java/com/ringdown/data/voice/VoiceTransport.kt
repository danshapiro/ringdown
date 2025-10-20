package com.ringdown.data.voice

import kotlinx.coroutines.flow.Flow

interface VoiceTransport {

    data class ConnectParameters(
        val deviceId: String,
        val signalingUrl: String,
        val authorizationToken: String? = null
    )

    data class AudioFrame(
        val pcmData: ByteArray,
        val sampleRateHz: Int,
        val channelCount: Int
    )

    suspend fun connect(parameters: ConnectParameters)

    suspend fun sendAudioFrame(frame: AudioFrame)

    fun receiveAudioFrames(): Flow<AudioFrame>

    suspend fun teardown()
}
