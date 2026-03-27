package com.ringdown.mobile.voice

import android.content.Context
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.ringdown.mobile.conversation.ConversationHistoryStore
import com.ringdown.mobile.data.BackendEnvironment
import com.ringdown.mobile.data.TextSessionStarter
import com.ringdown.mobile.domain.TextSessionBootstrap
import com.ringdown.mobile.text.TextSessionClient
import com.ringdown.mobile.voice.asr.AsrEvent
import com.ringdown.mobile.voice.asr.LocalAsrEngine
import java.time.Instant
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
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
        val appContext = ApplicationProvider.getApplicationContext<Context>()
        val historyScope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val historyStore = ConversationHistoryStore(
            PreferenceDataStoreFactory.create(scope = historyScope) {
                appContext.cacheDir.resolve(
                    "local_voice_session_controller_instrumented_history.preferences_pb"
                )
            },
            Dispatchers.Unconfined,
        )

        try {
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
                    override val events: MutableSharedFlow<AsrEvent> =
                        MutableSharedFlow(extraBufferCapacity = 1)

                    override suspend fun start() {}
                    override suspend fun stop() {}
                },
                greetingSpeechPlayer = GreetingSpeechPlayer(appContext),
                dispatcher = Dispatchers.Unconfined,
                mainDispatcher = Dispatchers.Unconfined,
                nowProvider = InstantProvider { Instant.parse("2025-11-02T00:00:00Z") },
                conversationHistoryStore = historyStore,
            )

            controller.start("assistant-b")

            val state = withTimeout(1_000) {
                controller.state.first { it is VoiceConnectionState.Failed }
            } as VoiceConnectionState.Failed

            assertEquals("unexpected scheme: wss", state.reason)
        } finally {
            historyScope.cancel()
        }
    }
}
