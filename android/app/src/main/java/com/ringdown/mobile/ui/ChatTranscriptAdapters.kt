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

internal fun combineTranscriptHistory(
    chatHistory: List<ChatMessage>,
    voiceTranscripts: List<TranscriptMessage>,
): List<TranscriptMessage> {
    val combined = mutableListOf<TranscriptMessage>()

    fun append(message: TranscriptMessage) {
        val last = combined.lastOrNull()
        val sameSpeaker = last?.speaker == message.speaker
        val sameText = last?.text == message.text
        val sameTimestamp = (last?.timestampIso ?: "") == (message.timestampIso ?: "")
        if (sameSpeaker && sameText && sameTimestamp) {
            return
        }
        combined += message
    }

    chatHistory.forEach { append(it.toTranscriptMessage()) }
    voiceTranscripts.forEach { append(it) }
    return combined
}
