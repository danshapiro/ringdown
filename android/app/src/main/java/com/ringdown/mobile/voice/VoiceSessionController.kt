package com.ringdown.mobile.voice

import android.media.projection.MediaProjection
import com.ringdown.mobile.BuildConfig
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
    private val managedController: ManagedVoiceSessionController,
    private val localController: LocalVoiceSessionController,
    @IoDispatcher dispatcher: CoroutineDispatcher,
) : VoiceSessionGateway {

    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val _state = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Idle)
    override val state: StateFlow<VoiceConnectionState> = _state.asStateFlow()

    private var activeMode: SessionMode? = null

    init {
        scope.launch {
            managedController.state.collectLatest { newState ->
                if (activeMode == SessionMode.MANAGED) {
                    _state.value = newState
                }
            }
        }
        scope.launch {
            localController.state.collectLatest { newState ->
                if (activeMode == SessionMode.LOCAL) {
                    _state.value = newState
                }
            }
        }
    }

    override fun start(deviceId: String, agent: String?) {
        val mode = if (shouldUseLocalAudio(agent)) SessionMode.LOCAL else SessionMode.MANAGED
        activeMode = mode
        android.util.Log.i("VoiceSessionSelector", "Starting voice session using mode=$mode")
        when (mode) {
            SessionMode.LOCAL -> localController.start(deviceId, agent)
            SessionMode.MANAGED -> managedController.start(deviceId, agent)
        }
    }

    override fun stop() {
        when (activeMode) {
            SessionMode.LOCAL -> localController.stop()
            SessionMode.MANAGED -> managedController.stop()
            null -> Unit
        }
        activeMode = null
        _state.value = VoiceConnectionState.Idle
    }

    override fun updateMediaProjection(token: MediaProjection?) {
        managedController.updateMediaProjection(token)
        localController.updateMediaProjection(token)
    }

    private fun shouldUseLocalAudio(agent: String?): Boolean {
        if (!BuildConfig.ENABLE_LOCAL_AUDIO_ALPHA) {
            return false
        }
        // Allow agent-specific overrides later; default to true when flag enabled.
        return true
    }

    private enum class SessionMode {
        MANAGED,
        LOCAL,
    }
}
