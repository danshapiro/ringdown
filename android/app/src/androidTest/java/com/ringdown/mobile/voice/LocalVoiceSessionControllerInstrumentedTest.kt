package com.ringdown.mobile.voice

import androidx.test.ext.junit.runners.AndroidJUnit4
import com.ringdown.mobile.data.BackendEnvironment
import com.ringdown.mobile.data.TextSessionStarter
import com.ringdown.mobile.domain.TextSessionBootstrap
import com.ringdown.mobile.text.TextSessionClient
import com.ringdown.mobile.voice.asr.AsrEvent
import com.ringdown.mobile.voice.asr.LocalAsrEngine
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class LocalVoiceSessionControllerInstrumentedTest {

    @Test
    fun startFailureEmitsLogcatEntry() = runBlocking {
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
            textSessionClient = object : TextSessionClient(
                backendEnvironment = object : BackendEnvironment() {
                    override fun baseUrl(): String = "https://example.invalid"
                },
                baseClient = OkHttpClient(),
                dispatcher = Dispatchers.Unconfined,
            ) {
                override suspend fun connect(bootstrap: TextSessionBootstrap) {
                    throw IllegalArgumentException("unexpected scheme: wss")
                }
            },
            asrEngine = object : LocalAsrEngine {
                override val events: MutableSharedFlow<AsrEvent> = MutableSharedFlow(extraBufferCapacity = 1)
                override suspend fun start() {}
                override suspend fun stop() {}
            },
            dispatcher = Dispatchers.Unconfined,
            mainDispatcher = Dispatchers.Unconfined,
            nowProvider = InstantProvider { Instant.parse("2025-11-02T00:00:00Z") },
        )

        controller.start("assistant-b")

        val state = withTimeout(1_000) {
            controller.state.first { it is VoiceConnectionState.Failed }
        } as VoiceConnectionState.Failed

        assertEquals("unexpected scheme: wss", state.reason)
    }
}
