package com.ringdown.mobile.voice

import android.util.Log
import com.ringdown.mobile.data.TextSessionGateway
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
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

private const val TAG = "LocalVoiceSession"

@Singleton
class LocalVoiceSessionController @Inject constructor(
    private val textSessionGateway: TextSessionGateway,
    private val textSessionClient: TextSessionClient,
    private val asrEngine: LocalAsrEngine,
    @IoDispatcher dispatcher: CoroutineDispatcher,
    @MainDispatcher private val mainDispatcher: CoroutineDispatcher,
    private val nowProvider: InstantProvider,
) {

    private val stateScope = CoroutineScope(SupervisorJob() + dispatcher)
    private val sessionScope = CoroutineScope(SupervisorJob() + dispatcher)
    private val sessionActive = AtomicBoolean(false)
    private val sessionLock = Mutex()

    private val _state = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Idle)
    val state: StateFlow<VoiceConnectionState> = _state

    private val transcripts: MutableList<TranscriptMessage> = mutableListOf()
    private val userDrafts: MutableMap<String, UserDraft> = mutableMapOf()
    private val outgoingStates: MutableMap<String, OutgoingUtteranceState> = mutableMapOf()
    private var assistantDraft: AssistantDraft? = null

    private var activeJobs: MutableList<Job> = mutableListOf()

    fun start(deviceId: String, agent: String?) {
        if (!sessionActive.compareAndSet(false, true)) {
            Log.w(TAG, "Session already running; ignoring start")
            return
        }

        sessionScope.launch {
            try {
                postState(VoiceConnectionState.Connecting)
                sessionLock.withLock {
                    cleanupSessionStateLocked()
                }

                registerCollectors()

                val bootstrap = textSessionGateway.startTextSession(agent)
                textSessionClient.connect(bootstrap)
                asrEngine.start()
            } catch (cancel: CancellationException) {
                sessionActive.set(false)
                throw cancel
            } catch (error: Exception) {
                Log.e(TAG, "Failed to start local voice session", error)
                sessionActive.set(false)
                runCatching { textSessionClient.disconnect() }
                runCatching { asrEngine.stop() }
                postState(VoiceConnectionState.Failed(error.message ?: "Unable to start session"))
            }
        }
    }

    fun stop() {
        if (!sessionActive.compareAndSet(true, false)) {
            return
        }
        sessionScope.launch {
            teardownSession()
            postState(VoiceConnectionState.Idle)
        }
    }

    private suspend fun registerCollectors() {
        val textEventsJob = sessionScope.launch {
            textSessionClient.events.collect { event ->
                when (event) {
                    is TextSessionEvent.Ready -> handleReady(event)
                    is TextSessionEvent.AssistantToken -> handleAssistantToken(event)
                    is TextSessionEvent.ToolEvent -> Log.d(TAG, "Tool event: ${event.event}")
                    is TextSessionEvent.ServerError -> handleServerError(event)
                    is TextSessionEvent.ConnectionClosed -> handleConnectionClosed(event)
                    is TextSessionEvent.ConnectionFailure -> handleConnectionFailure(event)
                    is TextSessionEvent.ProtocolError -> Log.w(TAG, "Protocol error ${event.reason}: ${event.detail}")
                    is TextSessionEvent.SendFailed -> Log.w(TAG, "Failed to send frame: ${event.payload}")
                }
            }
        }

        val asrEventsJob = sessionScope.launch {
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
        terminateWithFailure(event.reason ?: "Session closed")
    }

    private fun handleConnectionFailure(event: TextSessionEvent.ConnectionFailure) {
        Log.e(TAG, "Connection failure", event.error)
        terminateWithFailure(event.error.message ?: "Connection failure")
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
                textSessionClient.sendUserToken(delta, final = false, utteranceId = event.utteranceId)
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
                textSessionClient.sendUserToken("", final = true, utteranceId = event.utteranceId)
            } else {
                textSessionClient.sendUserMessage(payload, utteranceId = event.utteranceId)
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
        sessionScope.launch {
            teardownSession()
            postState(VoiceConnectionState.Failed(reason))
        }
    }

    private suspend fun teardownSession() {
        sessionLock.withLock {
            activeJobs.forEach { job ->
                job.cancel()
            }
            activeJobs.forEach { job ->
                try {
                    job.join()
                } catch (_: CancellationException) {
                    // ignore
                }
            }
            activeJobs.clear()
            runCatching { asrEngine.stop() }
            runCatching { textSessionClient.disconnect() }
            cleanupSessionStateLocked()
        }
    }

    private fun cleanupSessionStateLocked() {
        transcripts.clear()
        userDrafts.clear()
        outgoingStates.clear()
        assistantDraft = null
    }

    private fun publishTranscripts() {
        val snapshot = transcripts.toList()
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
    }

    private fun postState(newState: VoiceConnectionState) {
        stateScope.launch(mainDispatcher) {
            _state.value = newState
        }
    }

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
}
