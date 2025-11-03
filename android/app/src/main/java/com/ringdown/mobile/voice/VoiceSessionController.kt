package com.ringdown.mobile.voice

import com.ringdown.mobile.di.IoDispatcher
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

@Singleton
class VoiceSessionController @Inject constructor(
    private val localController: LocalVoiceSessionController,
    @IoDispatcher dispatcher: CoroutineDispatcher,
) : VoiceSessionGateway {

    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val _state = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Idle)
    override val state: StateFlow<VoiceConnectionState> = _state.asStateFlow()

    init {
        scope.launch {
            localController.state.collectLatest { newState ->
                _state.value = newState
            }
        }
    }

    override fun start(deviceId: String, agent: String?) {
        android.util.Log.i("VoiceSessionSelector", "Starting voice session using local pipeline")
        localController.start(deviceId, agent)
    }

    override fun stop() {
        localController.stop()
        _state.value = VoiceConnectionState.Idle
    }
}
