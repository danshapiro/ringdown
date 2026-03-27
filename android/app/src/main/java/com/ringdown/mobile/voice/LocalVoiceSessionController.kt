package com.ringdown.mobile.voice

import android.util.Log
import com.ringdown.mobile.conversation.ConversationHistoryStore
import com.ringdown.mobile.data.TextSessionStarter
import com.ringdown.mobile.di.IoDispatcher
import com.ringdown.mobile.di.MainDispatcher
import com.ringdown.mobile.text.TextSessionClient
import com.ringdown.mobile.text.TextSessionEvent
import com.ringdown.mobile.voice.asr.AsrEvent
import com.ringdown.mobile.voice.asr.LocalAsrEngine
import java.util.concurrent.atomic.AtomicBoolean
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject

private const val TAG = "LocalVoiceSession"

@Singleton
open class LocalVoiceSessionController @Inject constructor(
    private val textSessionStarter: TextSessionStarter,
    private val textSessionClient: TextSessionClient,
    private val asrEngine: LocalAsrEngine,
    private val greetingSpeechPlayer: GreetingSpeechGateway,
    @IoDispatcher dispatcher: CoroutineDispatcher,
    @MainDispatcher private val mainDispatcher: CoroutineDispatcher,
    private val nowProvider: InstantProvider,
    private val conversationHistoryStore: ConversationHistoryStore,
) {

    private val stateScope = CoroutineScope(SupervisorJob() + dispatcher)
    private val sessionScope = CoroutineScope(SupervisorJob() + dispatcher)
    private val sessionActive = AtomicBoolean(false)
    private val sessionLock = Mutex()

    private val _state = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Idle)
    open val state: StateFlow<VoiceConnectionState> = _state
    private val _reconnecting = MutableStateFlow(false)
    open val reconnecting: StateFlow<Boolean> = _reconnecting

    private val transcripts: MutableList<TranscriptMessage> = mutableListOf()
    private val userDrafts: MutableMap<String, UserDraft> = mutableMapOf()
    private val outgoingStates: MutableMap<String, OutgoingUtteranceState> = mutableMapOf()
    private var assistantDraft: AssistantDraft? = null
    private var currentSessionId: String? = null
    private var activeAgent: String? = null

    private var activeJobs: MutableList<Job> = mutableListOf()
    private var reconnectJob: Job? = null

    open fun start(agent: String?) {
        if (!sessionActive.compareAndSet(false, true)) {
            Log.w(TAG, "Session already running; ignoring start")
            return
        }

        sessionScope.launch {
            logStructured(
                level = "INFO",
                event = "local_voice.start_requested",
                fields = mapOf("agent" to agent),
            )
            try {
                postState(VoiceConnectionState.Connecting)
                sessionLock.withLock {
                    cleanupSessionStateLocked()
                }

                registerCollectors()

                val bootstrap = textSessionStarter.startTextSession(agent)
                conversationHistoryStore.setFromChat(bootstrap.history)
                activeAgent = bootstrap.agent.ifBlank { agent }
                textSessionClient.connect(bootstrap)
                asrEngine.start()
                _reconnecting.value = false
            } catch (cancel: CancellationException) {
                sessionActive.set(false)
                throw cancel
            } catch (error: Exception) {
                Log.e(TAG, "Failed to start local voice session", error)
                logStructured(
                    level = "ERROR",
                    event = "local_voice.start_failed",
                    fields = mapOf(
                        "message" to (error.message ?: "Unknown failure"),
                        "exceptionType" to error::class.java.simpleName,
                        "agent" to agent,
                    ),
                )
                sessionActive.set(false)
                runCatching { textSessionClient.disconnect() }
                runCatching { asrEngine.stop() }
                postState(VoiceConnectionState.Failed(error.message ?: "Unable to start session"))
            }
        }
    }

    open fun stop() {
        val wasActive = sessionActive.getAndSet(false)
        postState(VoiceConnectionState.Idle)
        if (!wasActive) {
            logStructured(
                level = "INFO",
                event = "local_voice.stop_noop",
                fields = emptyMap(),
            )
            return
        }
        sessionScope.launch {
            logStructured(
                level = "INFO",
                event = "local_voice.stop_started",
                fields = emptyMap(),
            )
            try {
                teardownSession()
            } finally {
                _reconnecting.value = false
                postState(VoiceConnectionState.Idle)
                logStructured(
                    level = "INFO",
                    event = "local_voice.stop_completed",
                    fields = emptyMap(),
                )
            }
        }
    }

    private suspend fun registerCollectors() {
        val textEventsJob = sessionScope.launch(start = CoroutineStart.UNDISPATCHED) {
            logStructured(
                level = "INFO",
                event = "local_voice.events_attached",
                fields = emptyMap(),
            )
            textSessionClient.events.collect { event ->
                logStructured(
                    level = "DEBUG",
                    event = "local_voice.event_received",
                    fields = mapOf("type" to event::class.java.simpleName),
                )
                when (event) {
                    is TextSessionEvent.Ready -> handleReady(event)
                    is TextSessionEvent.AssistantToken -> handleAssistantToken(event)
                    is TextSessionEvent.ToolEvent -> handleToolEvent(event)
                    is TextSessionEvent.ServerError -> handleServerError(event)
                    is TextSessionEvent.ConnectionClosed -> handleConnectionClosed(event)
                    is TextSessionEvent.ConnectionFailure -> handleConnectionFailure(event)
                    is TextSessionEvent.ProtocolError -> Log.w(TAG, "Protocol error ${event.reason}: ${event.detail}")
                    is TextSessionEvent.SendFailed -> Log.w(TAG, "Failed to send frame: ${event.payload}")
                }
            }
        }

        val asrEventsJob = sessionScope.launch(start = CoroutineStart.UNDISPATCHED) {
            logStructured(
                level = "INFO",
                event = "local_voice.asr_attached",
                fields = emptyMap(),
            )
            asrEngine.events.collect { event ->
                when (event) {
                    is AsrEvent.Partial -> handleAsrPartial(event)
                    is AsrEvent.Final -> handleAsrFinal(event)
                    is AsrEvent.Error -> handleAsrError(event)
                }
            }
        }

        activeJobs = mutableListOf(textEventsJob, asrEventsJob)
    }

    private fun handleReady(event: TextSessionEvent.Ready) {
        Log.i(TAG, "Session ready (sessionId=${event.sessionId ?: "unknown"})")
        currentSessionId = event.sessionId
        if (!event.agent.isNullOrBlank()) {
            activeAgent = event.agent
        }
        logStructured(
            level = "INFO",
            event = "local_voice.session_ready",
            fields = mapOf(
                "sessionId" to event.sessionId,
                "agent" to event.agent,
                "hasGreeting" to (event.greeting?.isNotBlank() == true),
            ),
        )
        seedGreetingIfPresent(event)
        publishTranscripts()
    }

    private fun handleAssistantToken(event: TextSessionEvent.AssistantToken) {
        val draft = assistantDraft ?: createAssistantDraft().also { assistantDraft = it }
        if (event.token.isNotEmpty()) {
            draft.builder.append(event.token)
            transcripts[draft.index] = transcripts[draft.index].copy(
                text = draft.builder.toString(),
                timestampIso = draft.timestampIso,
            )
            publishTranscripts()
        }
        if (event.final) {
            assistantDraft = null
        }
        logStructured(
            level = "INFO",
            event = "local_voice.assistant_token",
            fields = mapOf(
                "sessionId" to currentSessionId,
                "token" to event.token.take(MAX_TOKEN_LOG_LENGTH),
                "tokenLength" to event.token.length,
                "final" to event.final,
                "messageType" to event.messageType,
            ),
        )
    }

    private fun handleServerError(event: TextSessionEvent.ServerError) {
        val reason = buildString {
            append("Server error")
            event.code?.let { append(" (").append(it).append(')') }
            event.message?.let { append(": ").append(it) }
        }
        Log.e(TAG, reason)
        terminateWithFailure(reason)
    }

    private fun handleConnectionClosed(event: TextSessionEvent.ConnectionClosed) {
        val message = "Connection closed (${event.code}) ${event.reason.orEmpty()}"
        Log.i(TAG, message)
        if (!sessionActive.get() || event.reason == "client_shutdown") {
            logStructured(
                level = "INFO",
                event = "local_voice.connection_closed",
                fields = mapOf(
                    "code" to event.code,
                    "reason" to event.reason,
                ),
            )
            return
        }
        scheduleReconnect(event.reason ?: "Session closed")
    }

    private fun handleConnectionFailure(event: TextSessionEvent.ConnectionFailure) {
        Log.e(TAG, "Connection failure", event.error)
        if (!sessionActive.get()) {
            terminateWithFailure(event.error.message ?: "Connection failure")
            return
        }
        scheduleReconnect(event.error.message ?: "Connection failure")
    }

    private fun handleAsrPartial(event: AsrEvent.Partial) {
        val text = event.text
        if (text.isBlank()) {
            return
        }

        val draft = userDrafts.getOrPut(event.utteranceId) {
            val timestamp = nowProvider.now().toString()
            val index = transcripts.size
            transcripts.add(
                TranscriptMessage(
                    speaker = "user",
                    text = "",
                    timestampIso = timestamp,
                ),
            )
            UserDraft(index = index, timestampIso = timestamp)
        }

        transcripts[draft.index] = transcripts[draft.index].copy(text = text)
        publishTranscripts()

        val outgoing = outgoingStates.getOrPut(event.utteranceId) { OutgoingUtteranceState() }
        val previous = outgoing.emitted
        val delta = if (text.startsWith(previous)) {
            text.substring(previous.length)
        } else {
            sessionScope.launch {
                textSessionClient.sendCancel()
            }
            outgoing.emitted = ""
            text
        }

        if (delta.isNotEmpty()) {
            sessionScope.launch {
                textSessionClient.sendUserToken(
                    delta,
                    final = false,
                    utteranceId = event.utteranceId,
                    source = USER_MESSAGE_SOURCE,
                )
            }
            outgoing.emitted += delta
            outgoing.hasSentTokens = true
        }
    }

    private fun handleAsrFinal(event: AsrEvent.Final) {
        val finalText = event.text.trim()
        val draft = userDrafts.remove(event.utteranceId)
        val spoken = when {
            finalText.isNotEmpty() -> finalText
            draft != null && transcriptText(draft).isNotEmpty() -> transcriptText(draft)
            else -> ""
        }

        if (draft != null) {
            val index = draft.index
            if (index in transcripts.indices) {
                transcripts[index] = transcripts[index].copy(
                    text = spoken,
                    timestampIso = nowProvider.now().toString(),
                )
            }
        }
        publishTranscripts()

        val outgoing = outgoingStates.remove(event.utteranceId)
        val payload = finalText.ifBlank { outgoing?.emitted.orEmpty() }.trim()
        if (payload.isEmpty()) {
            return
        }

        sessionScope.launch {
            if (outgoing?.hasSentTokens == true) {
                textSessionClient.sendUserToken(
                    "",
                    final = true,
                    utteranceId = event.utteranceId,
                    source = USER_MESSAGE_SOURCE,
                )
            } else {
                textSessionClient.sendUserMessage(
                    payload,
                    utteranceId = event.utteranceId,
                    source = USER_MESSAGE_SOURCE,
                )
            }
        }
    }

    private fun handleAsrError(event: AsrEvent.Error) {
        Log.e(TAG, "ASR engine error", event.throwable)
        terminateWithFailure(event.throwable.message ?: "ASR engine error")
    }

    private fun terminateWithFailure(reason: String) {
        if (!sessionActive.compareAndSet(true, false)) {
            Log.w(TAG, "terminateWithFailure called but session already inactive")
            return
        }
        cancelReconnectJob()
        _reconnecting.value = false
        sessionScope.launch {
            teardownSession()
            postState(VoiceConnectionState.Failed(reason))
        }
    }

    private suspend fun teardownSession() {
        sessionLock.withLock {
            activeJobs.forEach { job -> job.cancel() }
            activeJobs.forEach { job ->
                val completed = withTimeoutOrNull(STOP_JOB_TIMEOUT_MILLIS) { job.join() }
                if (completed == null) {
                    logStructured(
                        level = "WARN",
                        event = "local_voice.stop_job_timeout",
                        fields = mapOf(
                            "job" to job::class.java.simpleName,
                            "active" to job.isActive,
                        ),
                    )
                }
            }
            activeJobs.clear()
            val asrResult = runCatching {
                withTimeoutOrNull(STOP_JOB_TIMEOUT_MILLIS) {
                    asrEngine.stop()
                    true
                }
            }
            when {
                asrResult.isFailure -> logStructured(
                    level = "ERROR",
                    event = "local_voice.stop_asr_error",
                    fields = mapOf("message" to (asrResult.exceptionOrNull()?.message ?: "unknown")),
                )
                asrResult.getOrNull() != true -> logStructured(
                    level = "WARN",
                    event = "local_voice.stop_asr_timeout",
                    fields = mapOf("timeoutMillis" to STOP_JOB_TIMEOUT_MILLIS),
                )
            }
            val disconnectResult = runCatching {
                withTimeoutOrNull(STOP_JOB_TIMEOUT_MILLIS) {
                    textSessionClient.disconnect()
                    true
                }
            }
            when {
                disconnectResult.isFailure -> logStructured(
                    level = "ERROR",
                    event = "local_voice.stop_disconnect_error",
                    fields = mapOf("message" to (disconnectResult.exceptionOrNull()?.message ?: "unknown")),
                )
                disconnectResult.getOrNull() != true -> logStructured(
                    level = "WARN",
                    event = "local_voice.stop_disconnect_timeout",
                    fields = mapOf("timeoutMillis" to STOP_JOB_TIMEOUT_MILLIS),
                )
            }
            cleanupSessionStateLocked()
        }
    }

    private fun cleanupSessionStateLocked() {
        transcripts.clear()
        userDrafts.clear()
        outgoingStates.clear()
        assistantDraft = null
        currentSessionId = null
        activeAgent = null
        cancelReconnectJob()
        greetingSpeechPlayer.stop()
    }

    private fun publishTranscripts() {
        val snapshot = transcripts.toList()
        conversationHistoryStore.setFromVoice(snapshot)
        stateScope.launch(mainDispatcher) {
            _state.value = VoiceConnectionState.Connected(snapshot)
        }
    }

    private fun seedGreetingIfPresent(event: TextSessionEvent.Ready) {
        val greeting = event.greeting?.trim().orEmpty()
        if (greeting.isEmpty()) {
            return
        }

        val speaker = event.agent?.takeIf { it.isNotBlank() } ?: "assistant"
        val alreadySeeded = transcripts.any { candidate ->
            candidate.speaker == speaker && candidate.text == greeting
        }
        if (alreadySeeded) {
            return
        }

        transcripts += TranscriptMessage(
            speaker = speaker,
            text = greeting,
            timestampIso = nowProvider.now().toString(),
        )
        publishTranscripts()
        greetingSpeechPlayer.speak(greeting)
    }

    private fun handleToolEvent(event: TextSessionEvent.ToolEvent) {
        val summary = buildToolSummary(event)
        val payload = event.payload.takeIf { it.isNotEmpty() }
        transcripts += TranscriptMessage(
            speaker = "tool",
            text = summary,
            timestampIso = nowProvider.now().toString(),
            messageType = event.event,
            toolPayload = payload,
        )
        publishTranscripts()
    }

    private fun postState(newState: VoiceConnectionState) {
        stateScope.launch(mainDispatcher) {
            _state.value = newState
        }
    }

    private fun scheduleReconnect(reason: String) {
        if (!sessionActive.get()) {
            return
        }
        if (reconnectJob?.isActive == true) {
            return
        }
        reconnectJob = sessionScope.launch {
            logStructured(
                level = "INFO",
                event = "local_voice.reconnect_start",
                fields = mapOf("reason" to reason),
            )
            postState(VoiceConnectionState.Connecting)
            val deadline = nowMillis() + RECONNECT_WINDOW_MILLIS
            var attempt = 0
            _reconnecting.value = true
            while (sessionActive.get() && nowMillis() < deadline) {
                if (attempt > 0) {
                    delay(RECONNECT_BACKOFF_MILLIS)
                }
                attempt += 1
                try {
                    restartSession()
                    logStructured(
                        level = "INFO",
                        event = "local_voice.reconnect_success",
                        fields = mapOf("attempt" to attempt),
                    )
                    _reconnecting.value = false
                    reconnectJob = null
                    return@launch
                } catch (error: Exception) {
                    logStructured(
                        level = "WARN",
                        event = "local_voice.reconnect_failed",
                        fields = mapOf(
                            "attempt" to attempt,
                            "message" to (error.message ?: "unknown"),
                            "exceptionType" to error::class.java.simpleName,
                        ),
                    )
                }
            }
            reconnectJob = null
            _reconnecting.value = false
            terminateWithFailure("Unable to reconnect: $reason")
        }
    }

    private suspend fun restartSession() {
        val previousAgent = activeAgent
        sessionLock.withLock {
            runCatching { asrEngine.stop() }
            val bootstrap = textSessionStarter.startTextSession(previousAgent)
            activeAgent = bootstrap.agent.ifBlank { previousAgent }
            textSessionClient.connect(bootstrap)
            asrEngine.start()
        }
    }

    private fun cancelReconnectJob() {
        reconnectJob?.cancel()
        reconnectJob = null
        _reconnecting.value = false
    }

    private fun nowMillis(): Long = nowProvider.now().toEpochMilli()

    private fun createAssistantDraft(): AssistantDraft {
        val timestamp = nowProvider.now().toString()
        val index = transcripts.size
        transcripts.add(
            TranscriptMessage(
                speaker = "assistant",
                text = "",
                timestampIso = timestamp,
            ),
        )
        return AssistantDraft(index = index, builder = StringBuilder(), timestampIso = timestamp)
    }

    private fun transcriptText(draft: UserDraft): String {
        val index = draft.index
        return if (index in transcripts.indices) {
            transcripts[index].text
        } else {
            ""
        }
    }

    private data class AssistantDraft(
        val index: Int,
        val builder: StringBuilder,
        val timestampIso: String,
    )

    private data class UserDraft(
        val index: Int,
        val timestampIso: String,
    )

    private data class OutgoingUtteranceState(
        var emitted: String = "",
        var hasSentTokens: Boolean = false,
    )

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
        val json = JSONObject(payload).toString()
        when (level.uppercase()) {
            "ERROR" -> Log.e(TAG, json)
            "WARNING", "WARN" -> Log.w(TAG, json)
            "DEBUG" -> Log.d(TAG, json)
            else -> Log.i(TAG, json)
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

    companion object {
        private const val MAX_TOKEN_LOG_LENGTH = 160
        private const val STOP_JOB_TIMEOUT_MILLIS = 5_000L
        private const val USER_MESSAGE_SOURCE = "android-local"
        private const val RECONNECT_WINDOW_MILLIS = 60_000L
        private const val RECONNECT_BACKOFF_MILLIS = 3_000L
    }
}
