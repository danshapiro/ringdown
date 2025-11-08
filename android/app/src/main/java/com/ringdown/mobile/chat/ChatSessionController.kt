package com.ringdown.mobile.chat

import android.util.Log
import com.ringdown.mobile.data.TextSessionStarter
import com.ringdown.mobile.di.IoDispatcher
import com.ringdown.mobile.di.MainDispatcher
import com.ringdown.mobile.text.TextSessionClient
import com.ringdown.mobile.text.TextSessionEvent
import com.ringdown.mobile.voice.InstantProvider
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

@Singleton
class ChatSessionController @Inject constructor(
    private val textSessionStarter: TextSessionStarter,
    private val textSessionClient: TextSessionClient,
    @IoDispatcher dispatcher: CoroutineDispatcher,
    @MainDispatcher private val mainDispatcher: CoroutineDispatcher,
    private val nowProvider: InstantProvider,
): ChatSessionGateway {

    private val sessionScope = CoroutineScope(SupervisorJob() + dispatcher)
    private val _state = MutableStateFlow<ChatConnectionState>(ChatConnectionState.Idle)
    override val state: StateFlow<ChatConnectionState> = _state.asStateFlow()

    private val sessionActive = AtomicBoolean(false)
    private val transcripts: MutableList<ChatMessage> = mutableListOf()
    private var assistantDraft: AssistantDraft? = null
    private var eventsJob: Job? = null
    private val stateMutex = Mutex()
    private var currentAgent: String? = null

    override fun start(agent: String?) {
        if (!sessionActive.compareAndSet(false, true)) {
            logStructured("INFO", "chat_session.already_active", mapOf("agent" to agent))
            return
        }
        sessionScope.launch {
            logStructured("INFO", "chat_session.start_requested", mapOf("agent" to agent))
            stateMutex.withLock {
                transcripts.clear()
                assistantDraft = null
            }
            emitState(ChatConnectionState.Connecting)
            registerEventCollectors()
            try {
                val bootstrap = textSessionStarter.startTextSession(agent)
                currentAgent = bootstrap.agent.ifBlank { agent }
                textSessionClient.connect(bootstrap)
            } catch (error: Exception) {
                logStructured(
                    "ERROR",
                    "chat_session.start_failed",
                    mapOf("message" to (error.message ?: "unknown")),
                )
                teardownSession()
                emitState(ChatConnectionState.Failed(error.message ?: "Unable to start chat."))
            }
        }
    }

    override fun stop() {
        if (!sessionActive.getAndSet(false)) {
            return
        }
        sessionScope.launch {
            logStructured("INFO", "chat_session.stop_requested", emptyMap())
            teardownSession()
            emitState(ChatConnectionState.Idle)
        }
    }

    override fun sendMessage(text: String) {
        val payload = text.trim()
        if (payload.isEmpty()) {
            return
        }
        sessionScope.launch {
            if (!sessionActive.get()) {
                emitState(ChatConnectionState.Failed("Chat is not connected."))
                return@launch
            }
            appendUserMessage(payload)
            try {
                textSessionClient.sendUserMessage(
                    text = payload,
                    utteranceId = null,
                    source = CHAT_MESSAGE_SOURCE,
                )
            } catch (error: Exception) {
                logStructured(
                    "ERROR",
                    "chat_session.send_failed",
                    mapOf("message" to (error.message ?: "unknown")),
                )
                emitState(ChatConnectionState.Failed("Failed to send message."))
            }
        }
    }

    private suspend fun registerEventCollectors() {
        eventsJob?.cancel()
        eventsJob = sessionScope.launch(start = CoroutineStart.UNDISPATCHED) {
            textSessionClient.events.collect { event ->
                when (event) {
                    is TextSessionEvent.Ready -> handleReady(event)
                    is TextSessionEvent.AssistantToken -> handleAssistantToken(event)
                    is TextSessionEvent.ToolEvent -> handleToolEvent(event)
                    is TextSessionEvent.ServerError -> handleServerError(event)
                    is TextSessionEvent.ConnectionClosed -> handleConnectionClosed(event)
                    is TextSessionEvent.ConnectionFailure -> handleConnectionFailure(event)
                    is TextSessionEvent.ProtocolError -> logStructured(
                        "WARN",
                        "chat_session.protocol_error",
                        mapOf("reason" to event.reason, "detail" to event.detail),
                    )
                    is TextSessionEvent.SendFailed -> logStructured(
                        "WARN",
                        "chat_session.send_failed_event",
                        mapOf("payload" to event.payload.take(64)),
                    )
                }
            }
        }
    }

    private suspend fun handleReady(event: TextSessionEvent.Ready) {
        logStructured(
            "INFO",
            "chat_session.ready",
            mapOf("sessionId" to event.sessionId, "agent" to event.agent),
        )
        val greeting = event.greeting?.trim().orEmpty()
        if (greeting.isNotEmpty()) {
            appendAssistantMessage(greeting, "greeting")
        }
        currentAgent = event.agent ?: currentAgent
        emitState(ChatConnectionState.Connected(currentAgent, transcriptsSnapshot()))
    }

    private suspend fun handleAssistantToken(event: TextSessionEvent.AssistantToken) {
        stateMutex.withLock {
            val draft = assistantDraft ?: createAssistantDraft(event.messageType)
            if (event.token.isNotEmpty()) {
                draft.builder.append(event.token)
                transcripts[draft.index] = transcripts[draft.index].copy(
                    text = draft.builder.toString(),
                )
            }
            if (event.final) {
                assistantDraft = null
            }
        }
        emitState(ChatConnectionState.Connected(currentAgent, transcriptsSnapshot()))
    }

    private suspend fun handleToolEvent(event: TextSessionEvent.ToolEvent) {
        val text = buildToolSummary(event)
        stateMutex.withLock {
            transcripts += ChatMessage(
                id = UUID.randomUUID().toString(),
                role = ChatMessageRole.TOOL,
                text = text,
                timestampIso = nowProvider.now().toString(),
                messageType = event.event,
                toolPayload = event.payload.takeIf { it.isNotEmpty() },
            )
        }
        emitState(ChatConnectionState.Connected(currentAgent, transcriptsSnapshot()))
    }

    private suspend fun handleServerError(event: TextSessionEvent.ServerError) {
        failSession(event.message ?: "Server error")
    }

    private suspend fun handleConnectionClosed(event: TextSessionEvent.ConnectionClosed) {
        val reason = event.reason?.ifBlank { null } ?: "Connection closed"
        failSession(reason)
    }

    private suspend fun handleConnectionFailure(event: TextSessionEvent.ConnectionFailure) {
        failSession(event.error.message ?: "Connection failure")
    }

    private suspend fun failSession(reason: String) {
        logStructured("ERROR", "chat_session.failed", mapOf("reason" to reason))
        sessionActive.set(false)
        teardownSession()
        emitState(ChatConnectionState.Failed(reason))
    }

    private suspend fun teardownSession() {
        eventsJob?.cancel()
        eventsJob = null
        runCatching {
            withTimeoutOrNull(STOP_TIMEOUT_MILLIS) {
                textSessionClient.disconnect()
            }
        }
        stateMutex.withLock {
            transcripts.clear()
            assistantDraft = null
        }
        currentAgent = null
    }

    private suspend fun appendAssistantMessage(text: String, messageType: String?) {
        stateMutex.withLock {
            transcripts += ChatMessage(
                id = UUID.randomUUID().toString(),
                role = ChatMessageRole.ASSISTANT,
                text = text,
                timestampIso = nowProvider.now().toString(),
                messageType = messageType,
            )
        }
    }

    private suspend fun appendUserMessage(text: String) {
        stateMutex.withLock {
            transcripts += ChatMessage(
                id = UUID.randomUUID().toString(),
                role = ChatMessageRole.USER,
                text = text,
                timestampIso = nowProvider.now().toString(),
            )
        }
        emitState(ChatConnectionState.Connected(currentAgent, transcriptsSnapshot()))
    }

    private suspend fun createAssistantDraft(messageType: String?): AssistantDraft {
        val draft = AssistantDraft(
            index = transcripts.size,
            builder = StringBuilder(),
            messageType = messageType,
        )
        transcripts += ChatMessage(
            id = UUID.randomUUID().toString(),
            role = ChatMessageRole.ASSISTANT,
            text = "",
            timestampIso = nowProvider.now().toString(),
            messageType = messageType,
        )
        assistantDraft = draft
        return draft
    }

    private suspend fun transcriptsSnapshot(): List<ChatMessage> = stateMutex.withLock { transcripts.toList() }

    private suspend fun emitState(state: ChatConnectionState) {
        withContext(mainDispatcher) {
            _state.value = state
        }
    }

    private fun buildToolSummary(event: TextSessionEvent.ToolEvent): String {
        if (event.payload.isEmpty()) {
            return event.event ?: "Tool event"
        }
        val summary = event.payload.entries.joinToString {
            val key = it.key
            val value = it.value ?: "null"
            key + "=" + value.toString()
        }
        return if (event.event.isNullOrBlank()) summary else event.event + ": " + summary
    }

    private fun logStructured(level: String, event: String, fields: Map<String, Any?>) {
        val payload = buildMap {
            put("severity", level)
            put("event", event)
            fields.forEach { (key, value) ->
                if (value != null) {
                    put(key, value)
                }
            }
        }
        val message = payload.toString()
        when (level.uppercase()) {
            "ERROR" -> Log.e(TAG, message)
            "WARNING", "WARN" -> Log.w(TAG, message)
            "DEBUG" -> Log.d(TAG, message)
            else -> Log.i(TAG, message)
        }
    }

    private data class AssistantDraft(
        val index: Int,
        val builder: StringBuilder,
        val messageType: String?,
    )

    companion object {
        private const val TAG = "ChatSessionController"
        private const val CHAT_MESSAGE_SOURCE = "android-chat"
        private const val STOP_TIMEOUT_MILLIS = 5_000L
    }
}
