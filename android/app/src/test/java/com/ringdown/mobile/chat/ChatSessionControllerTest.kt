package com.ringdown.mobile.chat

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.conversation.ConversationHistoryStore
import com.ringdown.mobile.data.TextSessionStarter
import com.ringdown.mobile.domain.TextSessionBootstrap
import com.ringdown.mobile.text.TextSessionClient
import com.ringdown.mobile.voice.InstantProvider
import java.nio.file.Files
import java.time.Instant
import kotlin.io.path.createTempDirectory
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
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
class ChatSessionControllerTest {

    @Test
    fun startSeedsTranscriptsFromPersistedHistory() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val store = createStore(dispatcher)
        store.setFromChat(
            listOf(
                ChatMessage(
                    id = "seed-1",
                    role = ChatMessageRole.ASSISTANT,
                    text = "Previously",
                    timestampIso = "2025-11-08T00:00:00Z",
                ),
            ),
        )
        advanceUntilIdle()

        val bootstrapReady = CompletableDeferred<Unit>()
        val controller = createController(
            dispatcher = dispatcher,
            store = store,
            starterOverride = TextSessionStarter { agent ->
                bootstrapReady.await()
                TextSessionBootstrap(
                    sessionId = "session-1",
                    sessionToken = "token",
                    resumeToken = null,
                    websocketPath = "/ws",
                    agent = agent ?: "tester",
                    expiresAtIso = Instant.now().toString(),
                    heartbeatIntervalSeconds = 15,
                    heartbeatTimeoutSeconds = 45,
                    tlsPins = emptyList(),
                    history = emptyList(),
                )
            },
        )
        controller.start("tester")
        runCurrent()

        val transcripts = controller.snapshotTranscripts()
        assertThat(transcripts).isNotEmpty()
        assertThat(transcripts.first().text).isEqualTo("Previously")

        bootstrapReady.complete(Unit)
        advanceUntilIdle()
    }

    @Test
    fun startOverwritesHistoryWithBootstrapSnapshot() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val store = createStore(dispatcher)
        store.setFromChat(emptyList())
        advanceUntilIdle()

        val bootstrapHistory = listOf(
            ChatMessage(
                id = "remote-1",
                role = ChatMessageRole.USER,
                text = "Remote user",
                timestampIso = "2025-11-08T02:00:00Z",
            ),
            ChatMessage(
                id = "remote-2",
                role = ChatMessageRole.ASSISTANT,
                text = "Remote assistant",
                timestampIso = "2025-11-08T02:01:00Z",
            ),
        )

        val controller = createController(dispatcher, store, bootstrapHistory)
        controller.start("tester")
        advanceUntilIdle()

        val transcripts = controller.snapshotTranscripts()
        assertThat(transcripts).hasSize(2)
        assertThat(transcripts.first().text).isEqualTo("Remote user")
        assertThat(store.history.value.first().text).isEqualTo("Remote user")
    }

    private fun createController(
        dispatcher: CoroutineDispatcher,
        store: ConversationHistoryStore,
        bootstrapHistory: List<ChatMessage> = emptyList(),
        starterOverride: TextSessionStarter? = null,
    ): ChatSessionController {
        val starter = starterOverride ?: TextSessionStarter { agent ->
            TextSessionBootstrap(
                sessionId = "session-1",
                sessionToken = "token",
                resumeToken = null,
                websocketPath = "/ws",
                agent = agent ?: "tester",
                expiresAtIso = Instant.now().toString(),
                heartbeatIntervalSeconds = 15,
                heartbeatTimeoutSeconds = 45,
                tlsPins = emptyList(),
                history = bootstrapHistory,
            )
        }
        val client = object : TextSessionClient(
            backendEnvironment = object : com.ringdown.mobile.data.BackendEnvironment() {
                override fun baseUrl(): String = "https://example.invalid"
            },
            baseClient = OkHttpClient(),
            dispatcher = dispatcher,
        ) {
            override suspend fun connect(bootstrap: TextSessionBootstrap) { /* no-op */ }
            override suspend fun disconnect() { /* no-op */ }
        }
        return ChatSessionController(
            textSessionStarter = starter,
            textSessionClient = client,
            conversationHistoryStore = store,
            dispatcher = dispatcher,
            mainDispatcher = dispatcher,
            nowProvider = InstantProvider { Instant.parse("2025-11-08T00:00:00Z") },
        )
    }

    private fun createStore(dispatcher: CoroutineDispatcher): ConversationHistoryStore {
        val scope = CoroutineScope(dispatcher + SupervisorJob())
        val file = Files.createTempFile("chat-history", ".preferences_pb").toFile()
        val dataStore = PreferenceDataStoreFactory.create(scope = scope) { file }
        return ConversationHistoryStore(dataStore, dispatcher)
    }

}
