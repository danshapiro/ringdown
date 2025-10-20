package com.ringdown.domain.usecase

import javax.inject.Inject
import javax.inject.Singleton

interface VoiceSessionCommands {
    fun start(deviceId: String)
    fun hangUp()
}

@Singleton
class VoiceSessionCommandDispatcher @Inject constructor(
    private val startVoiceSessionUseCase: StartVoiceSessionUseCase,
    private val hangUpVoiceSessionUseCase: HangUpVoiceSessionUseCase
) : VoiceSessionCommands {

    override fun start(deviceId: String) {
        startVoiceSessionUseCase(deviceId)
    }

    override fun hangUp() {
        hangUpVoiceSessionUseCase()
    }
}
