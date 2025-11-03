package com.ringdown.mobile.voice

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.BackendEnvironment
import com.ringdown.mobile.data.TextSessionGateway
import com.ringdown.mobile.domain.TextSessionBootstrap
import com.ringdown.mobile.text.TextSessionClient
import com.ringdown.mobile.text.TextSessionEvent
import com.ringdown.mobile.voice.asr.AsrEvent
import com.ringdown.mobile.voice.asr.LocalAsrEngine
import java.time.Instant
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlin.coroutines.CoroutineDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class LocalVoiceSessionControllerTest {

    @Test
    fun greetingSeededOnReadyEvent() = runTest {
        val dispatcher = backgroundScope.coroutineContext[CoroutineDispatcher]
            ?: error("Missing test dispatcher")
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
        val dispatcher = backgroundScope.coroutineContext[CoroutineDispatcher]
            ?: error("Missing test dispatcher")
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
        val textSessionGateway = object : TextSessionGateway {
            override suspend fun startTextSession(agent: String?): TextSessionBootstrap {
                return TextSessionBootstrap(
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
        }
        val asrEngine = object : LocalAsrEngine {
            override val events: MutableSharedFlow<AsrEvent> = MutableSharedFlow(extraBufferCapacity = 1)
            override suspend fun start() {}
            override suspend fun stop() {}
        }

        return LocalVoiceSessionController(
            textSessionGateway = textSessionGateway,
            textSessionClient = textSessionClient,
            asrEngine = asrEngine,
            dispatcher = dispatcher,
            mainDispatcher = dispatcher,
            nowProvider = InstantProvider { Instant.parse("2025-11-02T00:00:00Z") },
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
}
