package com.ringdown.mobile.ui

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.chat.ChatConnectionState
import com.ringdown.mobile.chat.ChatSessionGateway
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
import java.util.ArrayDeque
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.CoroutineDispatcher
import okhttp3.OkHttpClient
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class MainViewModelTest {

    @get:Rule
    val dispatcherRule = MainDispatcherRule()

    @Test
    fun pendingStatusAutoRetriesAndApproves() = runTest {
        val statuses = ArrayDeque<RegistrationStatus>().apply {
            add(
                RegistrationStatus.Pending(
                    message = "Waiting on approval",
                    pollAfterSeconds = 3,
                ),
            )
            add(
                RegistrationStatus.Approved(
                    agentName = "tester",
                    message = "Approved!",
                ),
            )
        }
        val gateway = FakeRegistrationGateway(statuses)

        val dispatcher = dispatcherRule.dispatcher
        val voiceController = RecordingVoiceController(dispatcher)
        val viewModel = MainViewModel(gateway, voiceController, FakeChatGateway())

        viewModel.onPermissionResult(true)

        advanceUntilIdle()

        advanceTimeBy(3_000)
        advanceUntilIdle()

        val state = viewModel.state.value
        assertThat(state.deviceId).isNotEmpty()
        assertThat(state.registrationStatus).isInstanceOf(RegistrationStatus.Approved::class.java)
        val approved = state.registrationStatus as RegistrationStatus.Approved
        assertThat(approved.agentName).isEqualTo("tester")
        assertThat(gateway.registerCalls).isEqualTo(2)
        assertThat(voiceController.starts).containsExactly("tester")
        assertThat(state.pendingAutoConnect).isFalse()
        assertThat(state.permissionRequestVersion).isEqualTo(0)
    }

    @Test
    fun autoConnectsAfterApprovalRequestsPermissionWhenDenied() = runTest {
        val gateway = FakeRegistrationGateway(
            ArrayDeque(
                listOf(
                    RegistrationStatus.Pending(
                        message = "Waiting",
                        pollAfterSeconds = 2,
                    ),
                    RegistrationStatus.Approved(
                        agentName = "tester",
                        message = "Approved!",
                    ),
                ),
            ),
        )

        val dispatcher = dispatcherRule.dispatcher
        val voiceController = RecordingVoiceController(dispatcher)
        val viewModel = MainViewModel(gateway, voiceController, FakeChatGateway())

        viewModel.onPermissionResult(false)

        advanceUntilIdle()
        advanceTimeBy(2_000)
        advanceUntilIdle()

        viewModel.onPermissionResult(false)
        advanceUntilIdle()

        val state = viewModel.state.value
        assertThat(state.pendingAutoConnect).isFalse()
        assertThat(state.showMicrophoneReminder).isTrue()
        assertThat(voiceController.starts).isEmpty()
        assertThat(state.permissionRequestVersion).isGreaterThan(0)
    }

    private class FakeRegistrationGateway(
        private val statuses: ArrayDeque<RegistrationStatus>,
    ) : RegistrationGateway {
        var registerCalls: Int = 0

        override suspend fun ensureDeviceId(): String = "device-123"

        override suspend fun register(
            deviceId: String,
            descriptor: DeviceDescriptor,
        ): RegistrationStatus {
            registerCalls += 1
            return if (statuses.isEmpty()) {
                RegistrationStatus.Approved(agentName = "tester", message = "fallback")
            } else {
                statuses.removeFirst()
            }
        }

        override suspend fun lastKnownAgent(): String? = null
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

        val starts = mutableListOf<String?>()

        override fun start(agent: String?) {
            starts += agent
            _state.value = VoiceConnectionState.Connecting
        }

        override fun stop() {
            _state.value = VoiceConnectionState.Idle
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
        override suspend fun sendUserToken(
            token: String,
            final: Boolean,
            utteranceId: String?,
            source: String?,
        ) {
        }

        override suspend fun sendUserMessage(
            text: String,
            utteranceId: String?,
            source: String?,
        ) {
        }
        override suspend fun sendCancel() {}
    }

    private class FakeChatGateway : ChatSessionGateway {
        private val _state = MutableStateFlow<ChatConnectionState>(ChatConnectionState.Idle)
        override val state: StateFlow<ChatConnectionState> = _state
        override fun start(agent: String?) {}
        override fun stop() {}
        override fun sendMessage(text: String) {}
    }
}
