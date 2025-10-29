package com.ringdown.mobile.voice

import android.util.Log
import co.daily.CallClientListener
import co.daily.model.CallState
import com.ringdown.mobile.data.VoiceSessionDataSource
import com.ringdown.mobile.domain.ManagedVoiceSession
import com.ringdown.mobile.di.IoDispatcher
import com.ringdown.mobile.di.MainDispatcher
import com.squareup.moshi.Json
import com.squareup.moshi.Moshi
import java.time.Duration
import java.time.Instant
import java.util.concurrent.atomic.AtomicBoolean
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val TAG = "VoiceSession"

fun interface InstantProvider {
    fun now(): Instant
}

sealed class VoiceConnectionState {
    object Idle : VoiceConnectionState()
    object Connecting : VoiceConnectionState()
    data class Connected(val transcripts: List<TranscriptMessage>) : VoiceConnectionState()
    data class Failed(val reason: String) : VoiceConnectionState()
}

data class TranscriptMessage(
    val speaker: String,
    val text: String,
    val timestampIso: String?,
)

@Singleton
class VoiceSessionController @Inject constructor(
    private val repository: VoiceSessionDataSource,
    private val callClientFactory: VoiceCallClientFactory,
    private val moshi: Moshi,
    @IoDispatcher dispatcher: CoroutineDispatcher,
    @MainDispatcher private val mainDispatcher: CoroutineDispatcher,
    @javax.inject.Named("voiceCallMinRefreshLead") private val minRefreshLead: Duration,
    private val nowProvider: InstantProvider,
) : VoiceSessionGateway {

    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val _state = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Idle)
    override val state: StateFlow<VoiceConnectionState> = _state.asStateFlow()

    private val transcripts: MutableList<TranscriptMessage> = mutableListOf()
    private val transcriptAdapter by lazy { moshi.adapter(TranscriptPayload::class.java) }

    private val sessionActive = AtomicBoolean(false)
    private var currentSession: ManagedVoiceSession? = null
    private var currentDeviceId: String = ""
    private var currentAgent: String? = null
    private var callClient: VoiceCallClient? = null
    private var callListener: CallClientListener? = null
    private var refreshJob: Job? = null

    override fun start(deviceId: String, agent: String?) {
        if (!sessionActive.compareAndSet(false, true)) {
            Log.w(TAG, "Voice session already running; ignoring start")
            return
        }

        currentDeviceId = deviceId
        currentAgent = agent

        scope.launch {
            try {
                _state.value = VoiceConnectionState.Connecting
                transcripts.clear()
                val session = repository.createSession(deviceId, agent)
                currentSession = session
                establishCall(session)
            } catch (error: Exception) {
                if (error is CancellationException) throw error
                Log.e(TAG, "Unable to start Daily session", error)
                _state.value = VoiceConnectionState.Failed(error.message ?: "Call failed")
                teardownClient()
                sessionActive.set(false)
            }
        }
    }

    override fun stop() {
        if (!sessionActive.compareAndSet(true, false)) {
            return
        }
        scope.launch {
            cancelRefresh()
            teardownClient()
            _state.value = VoiceConnectionState.Idle
        }
    }

    private suspend fun establishCall(session: ManagedVoiceSession) {
        cancelRefresh()

        val listener = buildListener()
        callListener = listener
        val client = withContext(mainDispatcher) {
            callClientFactory.create().also {
                it.attachListener(listener)
            }
        }
        callClient = client

        Log.i(
            TAG,
            "Joining Daily session ${session.sessionId} for agent=${session.agent} pipeline=${session.pipelineSessionId ?: "unknown"}",
        )

        withContext(mainDispatcher) {
            joinSession(client, session)
        }
        scheduleRefresh(session)
    }

    private fun buildListener(): CallClientListener = object : CallClientListener {
        override fun onCallStateUpdated(state: CallState) {
            when (state) {
                CallState.joined -> {
                    scope.launch {
                        _state.value = VoiceConnectionState.Connected(transcripts.toList())
                    }
                }
                CallState.left -> {
                    scope.launch {
                        if (sessionActive.get()) {
                            _state.value = VoiceConnectionState.Failed("Call disconnected")
                            stop()
                        } else {
                            _state.value = VoiceConnectionState.Idle
                        }
                    }
                }
                else -> Unit
            }
        }

        override fun onAppMessage(message: String, participantId: co.daily.model.ParticipantId) {
            scope.launch {
                handleTranscriptPayload(message)
            }
        }

        override fun onAppMessageFromRestApi(message: String) {
            scope.launch {
                handleTranscriptPayload(message)
            }
        }

        override fun onError(error: String) {
            scope.launch {
                Log.e(TAG, "Daily client error: $error")
                _state.value = VoiceConnectionState.Failed(error.ifBlank { "Call error" })
                stop()
            }
        }
    }

    private fun joinSession(client: VoiceCallClient, session: ManagedVoiceSession) {
        client.join(session) { errorMessage ->
            if (errorMessage != null) {
                scope.launch {
                    _state.value = VoiceConnectionState.Failed(errorMessage)
                    stop()
                }
            }
        }
    }

    private fun scheduleRefresh(session: ManagedVoiceSession) {
        val now = nowProvider.now()
        val ttl = Duration.between(now, session.expiresAt)
        if (ttl.isNegative) {
            scope.launch {
                _state.value = VoiceConnectionState.Failed("Call session expired")
                stop()
            }
            return
        }

        val calculated = ttl.multipliedBy(8).dividedBy(10)
        val delayDuration = if (calculated < minRefreshLead) minRefreshLead else calculated
        refreshJob = scope.launch {
            delay(delayDuration.toMillis())
            attemptTokenRefresh()
        }
    }

    private fun cancelRefresh() {
        refreshJob?.cancel()
        refreshJob = null
    }

    private suspend fun attemptTokenRefresh() {
        if (!sessionActive.get()) {
            return
        }
        val deviceId = currentDeviceId
        if (deviceId.isBlank()) {
            return
        }
        try {
            val newSession = repository.createSession(deviceId, currentAgent)
            currentSession = newSession
            val client = callClient
            if (client == null) {
                establishCall(newSession)
            } else {
                Log.i(TAG, "Attempting token refresh re-join")
                withContext(mainDispatcher) {
                    joinSession(client, newSession)
                }
                scheduleRefresh(newSession)
            }
        } catch (error: Exception) {
            if (error is CancellationException) throw error
            Log.e(TAG, "Token refresh failed", error)
            scope.launch {
                _state.value = VoiceConnectionState.Failed("Call session expired; tap reconnect")
                stop()
            }
        }
    }

    private suspend fun teardownClient() {
        withContext(mainDispatcher) {
            val listener = callListener
            if (listener != null) {
                try {
                    callClient?.detachListener(listener)
                } catch (error: Exception) {
                    Log.w(TAG, "Error removing Daily listener", error)
                }
            }

            try {
                callClient?.leave {
                    // ignore
                }
            } catch (error: Exception) {
                Log.w(TAG, "Error leaving Daily call", error)
            }

            try {
                callClient?.release()
            } catch (error: Exception) {
                Log.w(TAG, "Error releasing Daily resources", error)
            }

            callClient = null
            callListener = null
            currentSession = null
        }
    }

    private fun handleTranscriptPayload(raw: String) {
        val payload = try {
            transcriptAdapter.fromJson(raw)
        } catch (error: Exception) {
            Log.w(TAG, "Failed to parse transcript payload", error)
            null
        }
        if (payload == null) {
            return
        }
        if (payload.type != "transcript" || payload.text.isNullOrBlank()) {
            return
        }
        transcripts += TranscriptMessage(
            speaker = payload.speaker ?: "user",
            text = payload.text,
            timestampIso = payload.timestamp,
        )
        _state.value = VoiceConnectionState.Connected(transcripts.toList())
    }

    private data class TranscriptPayload(
        @Json(name = "type") val type: String?,
        @Json(name = "speaker") val speaker: String?,
        @Json(name = "text") val text: String?,
        @Json(name = "timestamp") val timestamp: String?,
    )
}
