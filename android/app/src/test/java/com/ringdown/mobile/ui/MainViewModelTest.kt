package com.ringdown.mobile.ui

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.chat.ChatConnectionState
import com.ringdown.mobile.chat.ChatSessionGateway
import com.ringdown.mobile.conversation.ConversationHistoryStore
import com.ringdown.mobile.data.BackendEnvironment
import com.ringdown.mobile.data.DeviceDescriptor
import com.ringdown.mobile.data.RegistrationGateway
import com.ringdown.mobile.data.TextSessionStarter
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.domain.TextSessionBootstrap
import com.ringdown.mobile.util.MainDispatcherRule
import com.ringdown.mobile.voice.GreetingSpeechGateway
import com.ringdown.mobile.voice.InstantProvider
import com.ringdown.mobile.voice.LocalVoiceSessionController
import com.ringdown.mobile.voice.VoiceConnectionState
import com.ringdown.mobile.voice.TranscriptMessage
import com.ringdown.mobile.voice.asr.AsrEvent
import com.ringdown.mobile.voice.asr.LocalAsrEngine
import java.nio.file.Files
import java.time.Instant
import java.util.ArrayDeque
import kotlin.io.path.createTempDirectory
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
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
        val historyStore = createHistoryStore(dispatcher)
        val voiceController = RecordingVoiceController(dispatcher, historyStore)
        val viewModel = MainViewModel(gateway, voiceController, FakeChatGateway(historyStore), historyStore)

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
        val historyStore = createHistoryStore(dispatcher)
        val voiceController = RecordingVoiceController(dispatcher, historyStore)
        val viewModel = MainViewModel(gateway, voiceController, FakeChatGateway(historyStore), historyStore)

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

    @Test
    fun openChatStartsSessionAndAllowsSend() = runTest {
        val statuses = ArrayDeque<RegistrationStatus>().apply {
            add(
                RegistrationStatus.Approved(
                    agentName = "tester",
                    message = "Approved!",
                ),
            )
        }
        val gateway = FakeRegistrationGateway(statuses)
        val dispatcher = dispatcherRule.dispatcher
        val historyStore = createHistoryStore(dispatcher)
        val voiceController = RecordingVoiceController(dispatcher, historyStore)
        val chatGateway = FakeChatGateway(historyStore)
        val viewModel = MainViewModel(gateway, voiceController, chatGateway, historyStore)

        advanceUntilIdle()

        viewModel.openChatSession()
        advanceUntilIdle()

        var state = viewModel.state.value
        assertThat(state.isChatVisible).isTrue()
        assertThat(chatGateway.starts).containsExactly("tester")
        assertThat(state.chatState).isInstanceOf(ChatConnectionState.Connected::class.java)

        viewModel.onChatInputChanged("Hello world")
        viewModel.sendChatMessage()
        advanceUntilIdle()

        state = viewModel.state.value
        assertThat(state.chatInput).isEmpty()
        assertThat(chatGateway.sentMessages).containsExactly("Hello world")
    }

    @Test
    fun switchChatToVoiceClosesChat() = runTest {
        val statuses = ArrayDeque<RegistrationStatus>().apply {
            add(RegistrationStatus.Approved(agentName = "tester", message = "Approved"))
        }
        val gateway = FakeRegistrationGateway(statuses)
        val dispatcher = dispatcherRule.dispatcher
        val historyStore = createHistoryStore(dispatcher)
        val voiceController = RecordingVoiceController(dispatcher, historyStore)
        val chatGateway = FakeChatGateway(historyStore)
        val viewModel = MainViewModel(gateway, voiceController, chatGateway, historyStore)

        advanceUntilIdle()
        viewModel.openChatSession()
        advanceUntilIdle()

        viewModel.onPermissionResult(true)
        advanceUntilIdle()

        viewModel.switchChatToVoice()
        advanceUntilIdle()

        val state = viewModel.state.value
        assertThat(state.isChatVisible).isFalse()
        assertThat(voiceController.starts.last()).isEqualTo("tester")
        assertThat(chatGateway.stopCount).isAtLeast(1)
    }

    @Test
    fun resetConversationClearsHistoryAndRestartsChat() = runTest {
        val statuses = ArrayDeque<RegistrationStatus>().apply {
            add(RegistrationStatus.Approved(agentName = "tester", message = "Approved"))
        }
        val gateway = FakeRegistrationGateway(statuses)
        val dispatcher = dispatcherRule.dispatcher
        val historyStore = createHistoryStore(dispatcher)
        val voiceController = RecordingVoiceController(dispatcher, historyStore)
        val chatGateway = FakeChatGateway(historyStore)
        val viewModel = MainViewModel(gateway, voiceController, chatGateway, historyStore)

        advanceUntilIdle()
        voiceController.emitTranscripts(
            listOf(
                TranscriptMessage("user", "Hi", "2025-11-08T00:00:00Z"),
            ),
        )
        viewModel.openChatSession()
        advanceUntilIdle()

        viewModel.resetConversation()
        advanceUntilIdle()

        assertThat(historyStore.history.value).isEmpty()
        assertThat(chatGateway.stopCount).isAtLeast(1)
        assertThat(chatGateway.starts.size).isEqualTo(2)
    }

    @Test
    fun resetConversationWhileVoiceActiveRestartsVoice() = runTest {
        val statuses = ArrayDeque<RegistrationStatus>().apply {
            add(RegistrationStatus.Approved(agentName = "tester", message = "Approved"))
        }
        val gateway = FakeRegistrationGateway(statuses)
        val dispatcher = dispatcherRule.dispatcher
        val historyStore = createHistoryStore(dispatcher)
        val voiceController = RecordingVoiceController(dispatcher, historyStore)
        val viewModel = MainViewModel(gateway, voiceController, FakeChatGateway(historyStore), historyStore)

        advanceUntilIdle()
        viewModel.onPermissionResult(true)
        voiceController.emit(VoiceConnectionState.Connected(emptyList()))
        advanceUntilIdle()

        viewModel.resetConversation()
        advanceUntilIdle()

        assertThat(voiceController.stopCount).isAtLeast(1)
        assertThat(voiceController.starts.size).isGreaterThan(0)
    }

    @Test
    fun openChatSeedsHistoryFromVoiceTranscripts() = runTest {
        val statuses = ArrayDeque<RegistrationStatus>().apply {
            add(RegistrationStatus.Approved(agentName = "tester", message = "Approved!"))
        }
        val gateway = FakeRegistrationGateway(statuses)
        val dispatcher = dispatcherRule.dispatcher
        val historyStore = createHistoryStore(dispatcher)
        val voiceController = RecordingVoiceController(dispatcher, historyStore)
        val chatGateway = FakeChatGateway(historyStore)
        val viewModel = MainViewModel(gateway, voiceController, chatGateway, historyStore)

        advanceUntilIdle()

        voiceController.emitTranscripts(
            listOf(
                TranscriptMessage(
                    speaker = "user",
                    text = "Hello",
                    timestampIso = "2025-01-01T00:00:00Z",
                ),
                TranscriptMessage(
                    speaker = "assistant",
                    text = "Hi there",
                    timestampIso = "2025-01-01T00:00:01Z",
                ),
            ),
        )

        advanceUntilIdle()

        viewModel.openChatSession()
        advanceUntilIdle()

        val history = viewModel.state.value.chatHistory
        assertThat(history).hasSize(2)
        assertThat(history.first().text).isEqualTo("Hello")
        assertThat(history.last().text).isEqualTo("Hi there")
    }

    @Test
    fun chatReconnectingFlagReflectsState() = runTest {
        val statuses = ArrayDeque<RegistrationStatus>().apply {
            add(RegistrationStatus.Approved(agentName = "tester", message = "Approved"))
        }
        val gateway = FakeRegistrationGateway(statuses)
        val dispatcher = dispatcherRule.dispatcher
        val historyStore = createHistoryStore(dispatcher)
        val voiceController = RecordingVoiceController(dispatcher, historyStore)
        val chatGateway = FakeChatGateway(historyStore)
        val viewModel = MainViewModel(gateway, voiceController, chatGateway, historyStore)

        advanceUntilIdle()
        viewModel.openChatSession()
        advanceUntilIdle()

        chatGateway.emit(ChatConnectionState.Connecting)
        advanceUntilIdle()
        assertThat(viewModel.state.value.isChatReconnecting).isTrue()

        chatGateway.emit(ChatConnectionState.Connected("tester", emptyList()))
        advanceUntilIdle()
        assertThat(viewModel.state.value.isChatReconnecting).isFalse()
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
        private val conversationHistoryStore: ConversationHistoryStore,
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
        greetingSpeechPlayer = object : GreetingSpeechGateway {
            override fun speak(text: String) {}
            override fun stop() {}
        },
        dispatcher = dispatcher,
        mainDispatcher = dispatcher,
        nowProvider = InstantProvider { Instant.parse("2025-01-01T00:00:00Z") },
        conversationHistoryStore = conversationHistoryStore,
    ) {
        private val _state = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Idle)
        override val state: StateFlow<VoiceConnectionState> = _state

        val starts = mutableListOf<String?>()
        var stopCount: Int = 0

        override fun start(agent: String?) {
            starts += agent
            _state.value = VoiceConnectionState.Connecting
        }

        override fun stop() {
            stopCount += 1
            _state.value = VoiceConnectionState.Idle
        }

        fun emit(value: VoiceConnectionState) {
            _state.value = value
        }

        fun emitTranscripts(transcripts: List<TranscriptMessage>) {
            _state.value = VoiceConnectionState.Connected(transcripts)
            conversationHistoryStore.setFromVoice(transcripts)
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

    private class FakeChatGateway(
        private val _conversationHistoryStore: ConversationHistoryStore,
    ) : ChatSessionGateway {
        private val _state = MutableStateFlow<ChatConnectionState>(ChatConnectionState.Idle)
        override val state: StateFlow<ChatConnectionState> = _state
        val starts = mutableListOf<String?>()
        val sentMessages = mutableListOf<String>()
        var stopCount: Int = 0

        override fun start(agent: String?) {
            starts += agent
            _state.value = ChatConnectionState.Connected(agent, emptyList())
        }

        override fun stop() {
            stopCount += 1
            _state.value = ChatConnectionState.Idle
        }

        override fun sendMessage(text: String) {
            sentMessages += text
        }

        fun emit(state: ChatConnectionState) {
            _state.value = state
        }
    }

    private fun createHistoryStore(dispatcher: CoroutineDispatcher): ConversationHistoryStore {
        val tempDir = createTempDirectory().toFile()
        val scope = CoroutineScope(dispatcher + SupervisorJob())
        val dataStore = PreferenceDataStoreFactory.create(scope = scope) {
            Files.createTempFile(tempDir.toPath(), "history", ".preferences_pb").toFile()
        }
        return ConversationHistoryStore(dataStore, dispatcher)
    }
}
