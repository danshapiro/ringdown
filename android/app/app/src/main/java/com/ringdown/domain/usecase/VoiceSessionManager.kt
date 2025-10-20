package com.ringdown.domain.usecase

import com.ringdown.BuildConfig
import com.ringdown.data.device.DeviceIdStorage
import com.ringdown.data.voice.AudioRouteController
import com.ringdown.data.voice.VoiceTransport
import com.ringdown.domain.model.VoiceSessionState
import java.time.Instant
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

interface VoiceSessionController {
    val state: StateFlow<VoiceSessionState>
    fun startSession()
    fun hangUp()
}

@Singleton
class VoiceSessionManager @Inject constructor(
    private val deviceIdStorage: DeviceIdStorage,
    private val voiceTransport: VoiceTransport,
    private val audioRouteController: AudioRouteController,
    @com.ringdown.di.IoDispatcher private val dispatcher: CoroutineDispatcher
) : VoiceSessionController {

    private val coroutineScope = CoroutineScope(SupervisorJob() + dispatcher)
    private val _state = MutableStateFlow<VoiceSessionState>(VoiceSessionState.Disconnected)
    override val state: StateFlow<VoiceSessionState> = _state.asStateFlow()

    override fun startSession() {
        if (_state.value is VoiceSessionState.Connecting || _state.value is VoiceSessionState.Active) {
            return
        }

        _state.value = VoiceSessionState.Connecting
        coroutineScope.launch {
            runCatching {
                val deviceId = deviceIdStorage.getOrCreate()
                val parameters = VoiceTransport.ConnectParameters(
                    deviceId = deviceId,
                    signalingUrl = BuildConfig.BACKEND_BASE_URL
                )

                voiceTransport.connect(parameters)
                audioRouteController.acquireVoiceRoute()
                _state.value = VoiceSessionState.Active(Instant.now())
            }.onFailure { error ->
                _state.value = VoiceSessionState.Error(error.message ?: "Unable to start voice session")
                teardownInternal()
            }
        }
    }

    override fun hangUp() {
        coroutineScope.launch {
            teardownInternal()
            _state.value = VoiceSessionState.Disconnected
        }
    }

    private suspend fun teardownInternal() = withContext(dispatcher) {
        runCatching { voiceTransport.teardown() }
        runCatching { audioRouteController.releaseVoiceRoute() }
    }
}
