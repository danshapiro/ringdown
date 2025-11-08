package com.ringdown.mobile.ui

import com.ringdown.mobile.chat.ChatMessage
import com.ringdown.mobile.chat.ChatMessageRole
import com.ringdown.mobile.voice.TranscriptMessage

internal fun ChatMessage.toTranscriptMessage(): TranscriptMessage {
    val speaker = when (role) {
        ChatMessageRole.USER -> "user"
        ChatMessageRole.ASSISTANT -> "assistant"
        ChatMessageRole.TOOL -> "tool"
    }
    return TranscriptMessage(
        speaker = speaker,
        text = text,
        timestampIso = timestampIso,
        messageType = messageType,
        toolPayload = toolPayload,
    )
}
