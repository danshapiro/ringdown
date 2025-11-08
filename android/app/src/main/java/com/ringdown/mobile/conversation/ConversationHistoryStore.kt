package com.ringdown.mobile.conversation

import com.ringdown.mobile.chat.ChatMessage
import com.ringdown.mobile.voice.TranscriptMessage
import com.ringdown.mobile.voice.toChatMessages
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

@Singleton
class ConversationHistoryStore @Inject constructor() {

    private val _history = MutableStateFlow<List<ChatMessage>>(emptyList())
    val history: StateFlow<List<ChatMessage>> = _history.asStateFlow()

    fun setFromVoice(transcripts: List<TranscriptMessage>) {
        update(transcripts.toChatMessages())
    }

    fun setFromChat(messages: List<ChatMessage>) {
        update(messages.toList())
    }

    fun clear() {
        update(emptyList())
    }

    private fun update(next: List<ChatMessage>) {
        if (_history.value == next) {
            return
        }
        _history.value = next
    }
}
