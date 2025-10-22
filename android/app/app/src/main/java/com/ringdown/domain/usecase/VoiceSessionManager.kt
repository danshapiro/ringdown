package com.ringdown.domain.usecase

import android.util.Log
import com.ringdown.BuildConfig
import com.ringdown.DebugFeatureFlags
import com.ringdown.data.device.DeviceIdStorage
import com.ringdown.data.voice.AudioRouteController
import com.ringdown.data.voice.VoiceDiagnosticType
import com.ringdown.data.voice.VoiceDiagnosticsReporter
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
    private val diagnostics: VoiceDiagnosticsReporter,
    @com.ringdown.di.IoDispatcher private val dispatcher: CoroutineDispatcher
) : VoiceSessionController {

    private companion object {
        private const val TAG = "VoiceSessionManager"
    }

    private val coroutineScope = CoroutineScope(SupervisorJob() + dispatcher)
    private val _state = MutableStateFlow<VoiceSessionState>(VoiceSessionState.Disconnected)
    override val state: StateFlow<VoiceSessionState> = _state.asStateFlow()

    override fun startSession() {
        if (_state.value is VoiceSessionState.Connecting || _state.value is VoiceSessionState.Active) {
            return
        }

        _state.value = VoiceSessionState.Connecting
        diagnostics.record(
            VoiceDiagnosticType.SESSION_STATE,
            "Session connecting"
        )
        coroutineScope.launch {
            runCatching {
                val deviceId = deviceIdStorage.getOrCreate()
                val parameters = VoiceTransport.ConnectParameters(
                    deviceId = deviceId,
                    signalingUrl = DebugFeatureFlags.backendBaseUrlOrDefault(
                        BuildConfig.BACKEND_BASE_URL
                    )
                )

                voiceTransport.connect(parameters)
                audioRouteController.acquireVoiceRoute()
                Log.i(TAG, "Voice session connected for device $deviceId using ${parameters.signalingUrl}")
                _state.value = VoiceSessionState.Active(Instant.now())
                diagnostics.record(
                    VoiceDiagnosticType.SESSION_STATE,
                    "Session active",
                    metadata = mapOf(
                        "deviceId" to deviceId,
                        "backend" to parameters.signalingUrl
                    )
                )
            }.onFailure { error ->
                Log.e(TAG, "Voice session start failed", error)
                val message = error.message ?: "Unable to start voice session"
                _state.value = VoiceSessionState.Error(message)
                diagnostics.record(
                    VoiceDiagnosticType.SESSION_STATE,
                    "Session error: $message"
                )
                teardownInternal()
            }
        }
    }

    override fun hangUp() {
        coroutineScope.launch {
            diagnostics.record(
                VoiceDiagnosticType.SESSION_STATE,
                "Manual hangup requested"
            )
            teardownInternal()
            _state.value = VoiceSessionState.Disconnected
        }
    }

    private suspend fun teardownInternal() = withContext(dispatcher) {
        runCatching { voiceTransport.teardown() }
        runCatching { audioRouteController.releaseVoiceRoute() }
        diagnostics.record(
            VoiceDiagnosticType.SESSION_STATE,
            "Session disconnected"
        )
        Log.d(TAG, "Voice session torn down")
    }
}
