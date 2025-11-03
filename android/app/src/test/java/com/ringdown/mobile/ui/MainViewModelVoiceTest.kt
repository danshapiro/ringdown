package com.ringdown.mobile.ui

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.BackendEnvironment
import com.ringdown.mobile.data.DeviceDescriptor
import com.ringdown.mobile.data.RegistrationGateway
import com.ringdown.mobile.data.TextSessionStarter
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.domain.TextSessionBootstrap
import com.ringdown.mobile.util.MainDispatcherRule
import com.ringdown.mobile.voice.InstantProvider
import com.ringdown.mobile.voice.LocalVoiceSessionController
import com.ringdown.mobile.voice.VoiceConnectionState
import com.ringdown.mobile.voice.asr.AsrEvent
import com.ringdown.mobile.voice.asr.LocalAsrEngine
import java.time.Instant
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.CoroutineDispatcher
import okhttp3.OkHttpClient
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class MainViewModelVoiceTest {

    @get:Rule
    val dispatcherRule = MainDispatcherRule()

    private val registrationGateway = object : RegistrationGateway {
        override suspend fun ensureDeviceId(): String = "device-test"
        override suspend fun register(deviceId: String, descriptor: DeviceDescriptor): RegistrationStatus {
            return RegistrationStatus.Approved(agentName = "agent-a", message = "ok")
        }
        override suspend fun lastKnownAgent(): String? = "agent-a"
    }

    @Test
    fun voiceStateUpdatesPropagateToUiState() = runTest {
        val dispatcher = dispatcherRule.dispatcher
        val voiceController = RecordingVoiceController(dispatcher)
        val viewModel = MainViewModel(registrationGateway, voiceController)

        voiceController.emit(VoiceConnectionState.Connecting)
        advanceUntilIdle()

        val state = viewModel.state.value
        assertThat(state.voiceState).isInstanceOf(VoiceConnectionState.Connecting::class.java)
    }

    @Test
    fun voiceFailureSurfacesError() = runTest {
        val dispatcher = dispatcherRule.dispatcher
        val voiceController = RecordingVoiceController(dispatcher)
        val viewModel = MainViewModel(registrationGateway, voiceController)

        voiceController.emit(VoiceConnectionState.Failed("failure"))
        advanceUntilIdle()

        val state = viewModel.state.value
        assertThat(state.errorMessage).isEqualTo("failure")
        assertThat(state.voiceState).isInstanceOf(VoiceConnectionState.Idle::class.java)
    }

    private class RecordingVoiceController(
        dispatcher: CoroutineDispatcher,
    ) : LocalVoiceSessionController(
        textSessionStarter = TextSessionStarter { agent ->
            TextSessionBootstrap(
                sessionId = "session",
                sessionToken = "token",
                resumeToken = null,
                websocketPath = "/ws",
                agent = agent.orEmpty(),
                expiresAtIso = "2025-01-01T00:00:00Z",
                heartbeatIntervalSeconds = 15,
                heartbeatTimeoutSeconds = 45,
                tlsPins = emptyList(),
            )
        },
        textSessionClient = TextSessionClientStub(dispatcher),
        asrEngine = object : LocalAsrEngine {
            override val events = MutableSharedFlow<AsrEvent>()
            override suspend fun start() {}
            override suspend fun stop() {}
        },
        dispatcher = dispatcher,
        mainDispatcher = dispatcher,
        nowProvider = InstantProvider { Instant.parse("2025-01-01T00:00:00Z") },
    ) {
        private val _state = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Idle)
        override val state: StateFlow<VoiceConnectionState> = _state

        override fun start(agent: String?) {
            _state.value = VoiceConnectionState.Connecting
        }

        override fun stop() {
            _state.value = VoiceConnectionState.Idle
        }

        fun emit(value: VoiceConnectionState) {
            _state.value = value
        }
    }

    private class TextSessionClientStub(
        dispatcher: CoroutineDispatcher,
    ) : com.ringdown.mobile.text.TextSessionClient(
        backendEnvironment = object : BackendEnvironment() {
            override fun baseUrl(): String = "https://example.com/"
        },
        baseClient = OkHttpClient(),
        dispatcher = dispatcher,
    ) {
        override suspend fun connect(bootstrap: TextSessionBootstrap) {}
        override suspend fun disconnect() {}
        override suspend fun sendUserToken(token: String, final: Boolean, utteranceId: String?) {}
        override suspend fun sendUserMessage(text: String, utteranceId: String?) {}
        override suspend fun sendCancel() {}
    }
}
