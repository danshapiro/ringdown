package com.ringdown.mobile.chat

import kotlinx.coroutines.flow.StateFlow

interface ChatSessionGateway {
    val state: StateFlow<ChatConnectionState>
    fun start(agent: String?)
    fun stop()
    fun sendMessage(text: String)
}
