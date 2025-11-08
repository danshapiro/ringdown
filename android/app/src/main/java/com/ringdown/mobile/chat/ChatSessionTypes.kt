package com.ringdown.mobile.chat

enum class ChatMessageRole {
    USER,
    ASSISTANT,
    TOOL,
}

data class ChatMessage(
    val id: String,
    val role: ChatMessageRole,
    val text: String,
    val timestampIso: String?,
    val messageType: String? = null,
    val toolPayload: Map<String, Any?>? = null,
)

sealed class ChatConnectionState {
    object Idle : ChatConnectionState()
    object Connecting : ChatConnectionState()
    data class Connected(val agent: String?, val messages: List<ChatMessage>) : ChatConnectionState()
    data class Failed(val reason: String) : ChatConnectionState()
}
