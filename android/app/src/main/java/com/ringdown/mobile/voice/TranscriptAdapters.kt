package com.ringdown.mobile.voice

import com.ringdown.mobile.chat.ChatMessage
import com.ringdown.mobile.chat.ChatMessageRole
import java.util.UUID

fun TranscriptMessage.toChatMessage(): ChatMessage {
    val role = when (speaker.lowercase()) {
        "user" -> ChatMessageRole.USER
        "tool" -> ChatMessageRole.TOOL
        else -> ChatMessageRole.ASSISTANT
    }
    return ChatMessage(
        id = UUID.randomUUID().toString(),
        role = role,
        text = text,
        timestampIso = timestampIso,
        messageType = messageType,
        toolPayload = toolPayload,
    )
}

fun List<TranscriptMessage>.toChatMessages(): List<ChatMessage> = map { it.toChatMessage() }
