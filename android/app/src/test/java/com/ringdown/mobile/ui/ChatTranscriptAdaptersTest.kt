package com.ringdown.mobile.ui

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.chat.ChatMessage
import com.ringdown.mobile.chat.ChatMessageRole
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
}
