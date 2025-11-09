package com.ringdown.mobile.ui

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.chat.ChatMessage
import com.ringdown.mobile.chat.ChatMessageRole
import com.ringdown.mobile.voice.TranscriptMessage
import org.junit.Test

class ChatTranscriptAdaptersTest {

    @Test
    fun toolMessagesPreserveMetadata() {
        val payload = mapOf("action" to "lookup", "status" to "complete")
        val message = ChatMessage(
            id = "tool-1",
            role = ChatMessageRole.TOOL,
            text = "Lookup finished.",
            timestampIso = "2025-11-08T04:00:00Z",
            messageType = "tool.lookup",
            toolPayload = payload,
        )

        val transcript = message.toTranscriptMessage()

        assertThat(transcript.speaker).isEqualTo("tool")
        assertThat(transcript.messageType).isEqualTo("tool.lookup")
        assertThat(transcript.toolPayload).isEqualTo(payload)
    }

    @Test
    fun combineTranscriptHistoryDeduplicatesSequentialDuplicates() {
        val chatHistory = listOf(
            ChatMessage(
                id = "voice-1",
                role = ChatMessageRole.ASSISTANT,
                text = "Hello there",
                timestampIso = "2025-11-08T01:00:00Z",
            ),
        )
        val voiceTranscripts = listOf(
            TranscriptMessage(
                speaker = "assistant",
                text = "Hello there",
                timestampIso = "2025-11-08T01:00:00Z",
            ),
            TranscriptMessage(
                speaker = "assistant",
                text = "What's your name?",
                timestampIso = "2025-11-08T01:00:02Z",
            ),
        )

        val combined = combineTranscriptHistory(chatHistory, voiceTranscripts)

        assertThat(combined).hasSize(2)
        assertThat(combined.first().text).isEqualTo("Hello there")
        assertThat(combined.last().text).isEqualTo("What's your name?")
    }
}
