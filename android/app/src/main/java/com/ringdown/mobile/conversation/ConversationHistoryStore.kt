package com.ringdown.mobile.conversation

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import com.ringdown.mobile.chat.ChatMessage
import com.ringdown.mobile.chat.ChatMessageRole
import com.ringdown.mobile.di.IoDispatcher
import com.ringdown.mobile.voice.TranscriptMessage
import com.ringdown.mobile.voice.toChatMessages
import com.squareup.moshi.JsonClass
import com.squareup.moshi.Moshi
import com.squareup.moshi.Types
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

@Singleton
class ConversationHistoryStore @Inject constructor(
    private val dataStore: DataStore<Preferences>,
    @IoDispatcher dispatcher: CoroutineDispatcher,
) {

    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val _history = MutableStateFlow<List<ChatMessage>>(emptyList())
    val history: StateFlow<List<ChatMessage>> = _history.asStateFlow()

    init {
        scope.launch {
            dataStore.data
                .map { prefs -> prefs[HISTORY_KEY] }
                .map { json -> decode(json) }
                .collect { decoded ->
                    _history.value = decoded
                }
        }
    }

    fun setFromVoice(transcripts: List<TranscriptMessage>) {
        persist(transcripts.toChatMessages())
    }

    fun setFromChat(messages: List<ChatMessage>) {
        persist(messages.toList())
    }

    fun reset() {
        persist(emptyList())
    }

    private fun persist(messages: List<ChatMessage>) {
        val trimmed = trim(messages)
        if (_history.value == trimmed) {
            return
        }
        _history.value = trimmed
        scope.launch {
            dataStore.edit { prefs ->
                if (trimmed.isEmpty()) {
                    prefs.remove(HISTORY_KEY)
                } else {
                    prefs[HISTORY_KEY] = encode(trimmed)
                }
            }
        }
    }

    private fun trim(messages: List<ChatMessage>): List<ChatMessage> {
        if (messages.size <= MAX_HISTORY) {
            return messages
        }
        return messages.takeLast(MAX_HISTORY)
    }

    private fun encode(messages: List<ChatMessage>): String {
        val persisted = messages.map { it.toPersisted() }
        return messageAdapter.toJson(persisted)
    }

    private fun decode(json: String?): List<ChatMessage> {
        if (json.isNullOrBlank()) {
            return emptyList()
        }
        return runCatching {
            messageAdapter.fromJson(json)?.map { it.toChatMessage() } ?: emptyList()
        }.getOrDefault(emptyList())
    }

    companion object {
        private val HISTORY_KEY = stringPreferencesKey("conversation_history_json")
        private val moshi = Moshi.Builder()
            .add(KotlinJsonAdapterFactory())
            .build()
        private val messageAdapter = moshi.adapter<List<PersistedMessage>>(
            Types.newParameterizedType(List::class.java, PersistedMessage::class.java),
        )
        private const val MAX_HISTORY = 200
    }
}

@JsonClass(generateAdapter = true)
private data class PersistedMessage(
    val id: String,
    val role: String,
    val text: String,
    val timestampIso: String?,
    val messageType: String?,
    val toolPayload: Map<String, Any?>?,
)

private fun ChatMessage.toPersisted(): PersistedMessage = PersistedMessage(
    id = id,
    role = role.name,
    text = text,
    timestampIso = timestampIso,
    messageType = messageType,
    toolPayload = toolPayload,
)

private fun PersistedMessage.toChatMessage(): ChatMessage = ChatMessage(
    id = id,
    role = runCatching { ChatMessageRole.valueOf(role) }.getOrDefault(ChatMessageRole.ASSISTANT),
    text = text,
    timestampIso = timestampIso,
    messageType = messageType,
    toolPayload = toolPayload,
)
