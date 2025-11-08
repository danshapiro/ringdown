package com.ringdown.mobile.voice

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.conversation.ConversationHistoryStore
import com.ringdown.mobile.data.BackendEnvironment
import com.ringdown.mobile.data.TextSessionStarter
import com.ringdown.mobile.domain.TextSessionBootstrap
import com.ringdown.mobile.text.TextSessionClient
import com.ringdown.mobile.text.TextSessionEvent
import com.ringdown.mobile.voice.asr.AsrEvent
import com.ringdown.mobile.voice.asr.LocalAsrEngine
import java.time.Instant
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLog

@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class LocalVoiceSessionControllerTest {

    @Test
    fun greetingSeededOnReadyEvent() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val controller = createController(dispatcher)

        invokeHandleReady(
            controller,
            TextSessionEvent.Ready(
                sessionId = "session-1",
                agent = "assistant-b",
                greeting = "Welcome aboard.",
                heartbeatIntervalSeconds = 15,
                heartbeatTimeoutSeconds = 45,
            ),
        )
        runCurrent()

        val state = controller.state.value
        require(state is VoiceConnectionState.Connected)
        assertThat(state.transcripts).hasSize(1)
        val greeting = state.transcripts.first()
        assertThat(greeting.speaker).isEqualTo("assistant-b")
        assertThat(greeting.text).isEqualTo("Welcome aboard.")
        assertThat(greeting.timestampIso).isEqualTo("2025-11-02T00:00:00Z")
    }

    @Test
    fun duplicateReadyEventsDoNotDuplicateGreeting() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val controller = createController(dispatcher)
        val readyEvent = TextSessionEvent.Ready(
            sessionId = "session-1",
            agent = "assistant-b",
            greeting = "Welcome aboard.",
            heartbeatIntervalSeconds = 15,
            heartbeatTimeoutSeconds = 45,
        )

        repeat(2) {
            invokeHandleReady(controller, readyEvent)
            runCurrent()
        }

        val state = controller.state.value
        require(state is VoiceConnectionState.Connected)
        assertThat(state.transcripts).hasSize(1)
        val greeting = state.transcripts.first()
        assertThat(greeting.text).isEqualTo("Welcome aboard.")
    }

    @Test
    fun assistantTokensAccumulateTranscript() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val controller = createController(dispatcher)

        val readyEvent = TextSessionEvent.Ready(
            sessionId = "session-99",
            agent = "assistant-b",
            greeting = null,
            heartbeatIntervalSeconds = 15,
            heartbeatTimeoutSeconds = 45,
        )
        invokeHandleReady(controller, readyEvent)
        runCurrent()

        invokeHandleAssistantToken(
            controller,
            TextSessionEvent.AssistantToken(token = "Hello ", final = false, messageType = null),
        )
        runCurrent()

        invokeHandleAssistantToken(
            controller,
            TextSessionEvent.AssistantToken(token = "there!", final = true, messageType = null),
        )
        runCurrent()

        val state = controller.state.value
        require(state is VoiceConnectionState.Connected)
        assertThat(state.transcripts).hasSize(1)
        val assistantLine = state.transcripts.first()
        assertThat(assistantLine.text).isEqualTo("Hello there!")
        assertThat(assistantLine.speaker).isEqualTo("assistant")
    }

    @Test
    fun startFailureLogsStructuredMessage() = runTest {
        ShadowLog.clear()
        val dispatcher = StandardTestDispatcher(testScheduler)

        val backendEnvironment = object : BackendEnvironment() {
            override fun baseUrl(): String = "https://example.invalid"
        }
        val failingClient = object : TextSessionClient(
            backendEnvironment,
            OkHttpClient(),
            dispatcher,
        ) {
            override suspend fun connect(bootstrap: TextSessionBootstrap) {
                throw IllegalArgumentException("unexpected scheme: wss")
            }
        }

        val controller = LocalVoiceSessionController(
            textSessionStarter = TextSessionStarter { agent ->
                TextSessionBootstrap(
                    sessionId = "session-err",
                    sessionToken = "token",
                    resumeToken = "resume",
                    websocketPath = "/ws",
                    agent = agent ?: "assistant-b",
                    expiresAtIso = "2025-11-02T00:10:00Z",
                    heartbeatIntervalSeconds = 15,
                    heartbeatTimeoutSeconds = 45,
                    tlsPins = emptyList(),
                )
            },
            textSessionClient = failingClient,
            asrEngine = object : LocalAsrEngine {
                override val events: MutableSharedFlow<AsrEvent> = MutableSharedFlow(extraBufferCapacity = 1)
                override suspend fun start() {}
                override suspend fun stop() {}
            },
            dispatcher = dispatcher,
            mainDispatcher = dispatcher,
            nowProvider = InstantProvider { Instant.parse("2025-11-02T00:00:00Z") },
            conversationHistoryStore = ConversationHistoryStore(),
        )

        controller.start("assistant-b")
        runCurrent()

        val logs = ShadowLog.getLogs()
        val structuredLog = logs.firstOrNull { entry ->
            entry.tag == "LocalVoiceSession" && entry.msg.contains("\"event\":\"local_voice.start_failed\"")
        }
        assertThat(structuredLog).isNotNull()
        assertThat(structuredLog!!.msg).contains("unexpected scheme: wss")

        val state = controller.state.value
        assertThat(state).isInstanceOf(VoiceConnectionState.Failed::class.java)
    }

    @Test
    fun reconnectAfterStopStartsNewSession() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val connectCalls = mutableListOf<TextSessionBootstrap>()
        val disconnectCalls = AtomicInteger(0)

        val backendEnvironment = object : BackendEnvironment() {
            override fun baseUrl(): String = "https://example.invalid"
        }
        val textSessionClient = object : TextSessionClient(
            backendEnvironment,
            OkHttpClient(),
            dispatcher,
        ) {
            override suspend fun connect(bootstrap: TextSessionBootstrap) {
                connectCalls += bootstrap
            }

            override suspend fun disconnect() {
                disconnectCalls.incrementAndGet()
            }
        }
        val starter = TextSessionStarter { agent ->
            TextSessionBootstrap(
                sessionId = "session-${connectCalls.size + 1}",
                sessionToken = "token-${connectCalls.size + 1}",
                resumeToken = "resume-${connectCalls.size + 1}",
                websocketPath = "/ws",
                agent = agent ?: "assistant-b",
                expiresAtIso = "2025-11-02T00:10:00Z",
                heartbeatIntervalSeconds = 15,
                heartbeatTimeoutSeconds = 45,
                tlsPins = emptyList(),
            )
        }
        val asrEngine = object : LocalAsrEngine {
            override val events: MutableSharedFlow<AsrEvent> = MutableSharedFlow(extraBufferCapacity = 1)
            override suspend fun start() {}
            override suspend fun stop() {}
        }

        val controller = LocalVoiceSessionController(
            textSessionStarter = starter,
            textSessionClient = textSessionClient,
            asrEngine = asrEngine,
            dispatcher = dispatcher,
            mainDispatcher = dispatcher,
            nowProvider = InstantProvider { Instant.parse("2025-11-02T00:00:00Z") },
            conversationHistoryStore = ConversationHistoryStore(),
        )

        controller.start("assistant-b")
        runCurrent()
        controller.stop()
        runCurrent()
        controller.start("assistant-b")
        runCurrent()

        assertThat(connectCalls).hasSize(2)
        assertThat(disconnectCalls.get()).isEqualTo(1)
    }

    @Test
    fun connectionFailureTriggersReconnect() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val connectCalls = mutableListOf<TextSessionBootstrap>()

        val backendEnvironment = object : BackendEnvironment() {
            override fun baseUrl(): String = "https://example.invalid"
        }
        val textSessionClient = object : TextSessionClient(
            backendEnvironment,
            OkHttpClient(),
            dispatcher,
        ) {
            override suspend fun connect(bootstrap: TextSessionBootstrap) {
                connectCalls += bootstrap
            }
        }
        val starter = TextSessionStarter { agent ->
            TextSessionBootstrap(
                sessionId = "session-${connectCalls.size + 1}",
                sessionToken = "token-${connectCalls.size + 1}",
                resumeToken = "resume-${connectCalls.size + 1}",
                websocketPath = "/ws",
                agent = agent ?: "assistant-b",
                expiresAtIso = "2025-11-02T00:10:00Z",
                heartbeatIntervalSeconds = 15,
                heartbeatTimeoutSeconds = 45,
                tlsPins = emptyList(),
            )
        }
        val asrEngine = object : LocalAsrEngine {
            override val events: MutableSharedFlow<AsrEvent> = MutableSharedFlow(extraBufferCapacity = 1)
            override suspend fun start() {}
            override suspend fun stop() {}
        }

        val controller = LocalVoiceSessionController(
            textSessionStarter = starter,
            textSessionClient = textSessionClient,
            asrEngine = asrEngine,
            dispatcher = dispatcher,
            mainDispatcher = dispatcher,
            nowProvider = InstantProvider { Instant.parse("2025-11-02T00:00:00Z") },
            conversationHistoryStore = ConversationHistoryStore(),
        )

        controller.start("assistant-b")
        runCurrent()

        val readyEvent = TextSessionEvent.Ready(
            sessionId = "session-1",
            agent = "assistant-b",
            greeting = null,
            heartbeatIntervalSeconds = 15,
            heartbeatTimeoutSeconds = 45,
        )
        invokeHandleReady(controller, readyEvent)
        runCurrent()

        invokeHandleConnectionFailure(
            controller,
            TextSessionEvent.ConnectionFailure(RuntimeException("boom")),
        )
        runCurrent()
        advanceTimeBy(100)
        runCurrent()

        assertThat(connectCalls).hasSize(2)
    }

    private fun createController(
        dispatcher: CoroutineDispatcher,
    ): LocalVoiceSessionController {
        val backendEnvironment = object : BackendEnvironment() {
            override fun baseUrl(): String = "https://example.invalid"
        }
        val textSessionClient = TextSessionClient(
            backendEnvironment,
            OkHttpClient(),
            dispatcher,
        )
        val textSessionStarter = TextSessionStarter { _ ->
            TextSessionBootstrap(
                sessionId = "session-1",
                sessionToken = "token",
                resumeToken = "resume",
                websocketPath = "/ws",
                agent = "assistant-b",
                expiresAtIso = "2025-11-02T00:10:00Z",
                heartbeatIntervalSeconds = 15,
                heartbeatTimeoutSeconds = 45,
                tlsPins = emptyList(),
            )
        }
        val asrEngine = object : LocalAsrEngine {
            override val events: MutableSharedFlow<AsrEvent> = MutableSharedFlow(extraBufferCapacity = 1)
            override suspend fun start() {}
            override suspend fun stop() {}
        }

        return LocalVoiceSessionController(
            textSessionStarter = textSessionStarter,
            textSessionClient = textSessionClient,
            asrEngine = asrEngine,
            dispatcher = dispatcher,
            mainDispatcher = dispatcher,
            nowProvider = InstantProvider { Instant.parse("2025-11-02T00:00:00Z") },
            conversationHistoryStore = ConversationHistoryStore(),
        )
    }

    private fun invokeHandleReady(
        controller: LocalVoiceSessionController,
        event: TextSessionEvent.Ready,
    ) {
        val method = LocalVoiceSessionController::class.java.getDeclaredMethod(
            "handleReady",
            TextSessionEvent.Ready::class.java,
        )
        method.isAccessible = true
        method.invoke(controller, event)
    }

    private fun invokeHandleAssistantToken(
        controller: LocalVoiceSessionController,
        event: TextSessionEvent.AssistantToken,
    ) {
        val method = LocalVoiceSessionController::class.java.getDeclaredMethod(
            "handleAssistantToken",
            TextSessionEvent.AssistantToken::class.java,
        )
        method.isAccessible = true
        method.invoke(controller, event)
    }

    private fun invokeHandleConnectionFailure(
        controller: LocalVoiceSessionController,
        event: TextSessionEvent.ConnectionFailure,
    ) {
        val method = LocalVoiceSessionController::class.java.getDeclaredMethod(
            "handleConnectionFailure",
            TextSessionEvent.ConnectionFailure::class.java,
        )
        method.isAccessible = true
        method.invoke(controller, event)
    }
}
