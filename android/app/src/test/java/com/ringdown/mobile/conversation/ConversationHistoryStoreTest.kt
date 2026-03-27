package com.ringdown.mobile.conversation

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.chat.ChatMessage
import com.ringdown.mobile.chat.ChatMessageRole
import com.ringdown.mobile.voice.TranscriptMessage
import java.io.File
import java.nio.file.Files
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ConversationHistoryStoreTest {

    @Test
    fun persistsChatHistoryToDataStore() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val backingFile = Files.createTempFile("history", ".preferences_pb").toFile()
        val (store, closeStore) = createStore(dispatcher, backingFile)

        val message = ChatMessage(
            id = "1",
            role = ChatMessageRole.USER,
            text = "Hello",
            timestampIso = "2025-11-08T00:00:00Z",
        )
        store.setFromChat(listOf(message))
        advanceUntilIdle()
        closeStore()

        val (restored, closeRestored) = createStore(dispatcher, backingFile)
        advanceUntilIdle()

        val history = restored.history.value
        assertThat(history).hasSize(1)
        assertThat(history.first().text).isEqualTo("Hello")
        assertThat(history.first().role).isEqualTo(ChatMessageRole.USER)
        closeRestored()
    }

    @Test
    fun trimsHistoryToMaximumSize() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val (store, closeStore) = createStore(dispatcher)

        val messages = (0 until 250).map { index ->
            ChatMessage(
                id = "id-$index",
                role = ChatMessageRole.ASSISTANT,
                text = "msg-$index",
                timestampIso = null,
            )
        }
        store.setFromChat(messages)
        advanceUntilIdle()

        val history = store.history.value
        assertThat(history).hasSize(200)
        assertThat(history.first().text).isEqualTo("msg-50")
        assertThat(history.last().text).isEqualTo("msg-249")
        closeStore()
    }

    @Test
    fun convertsVoiceTranscriptsToChatMessages() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val (store, closeStore) = createStore(dispatcher)

        store.setFromVoice(
            listOf(
                TranscriptMessage("user", "Hi", "2025-11-08T00:00:00Z"),
                TranscriptMessage("assistant", "Hello", "2025-11-08T00:00:01Z"),
            ),
        )
        advanceUntilIdle()

        val history = store.history.value
        assertThat(history.map { it.text }).containsExactly("Hi", "Hello").inOrder()
        assertThat(history.first().role).isEqualTo(ChatMessageRole.USER)
        assertThat(history.last().role).isEqualTo(ChatMessageRole.ASSISTANT)
        closeStore()
    }

    private fun createStore(
        dispatcher: CoroutineDispatcher,
        backingFile: File = Files.createTempFile("history", ".preferences_pb").toFile(),
    ): Pair<ConversationHistoryStore, () -> Unit> {
        val scope = CoroutineScope(dispatcher + SupervisorJob())
        val dataStore = PreferenceDataStoreFactory.create(scope = scope) { backingFile }
        val store = ConversationHistoryStore(dataStore, dispatcher)
        val closeAction = {
            scope.cancel()
        }
        return store to closeAction
    }
}
