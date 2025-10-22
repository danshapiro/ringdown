package com.ringdown.data.voice

import android.util.Log
import java.util.concurrent.CopyOnWriteArrayList
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

private const val DIAGNOSTIC_TAG = "VoiceDiagnostics"

/** High-level categories for handset voice diagnostics. */
enum class VoiceDiagnosticType {
    CONNECT_ATTEMPT,
    CONNECT_SUCCEEDED,
    CONNECT_FAILED,
    AUDIO_DEVICE_ERROR,
    AUDIO_DEVICE_STATE,
    ICE_STATE,
    PEER_STATE,
    LOCAL_CANDIDATE_PUBLISHED,
    REMOTE_CANDIDATE_APPLIED,
    ICE_SERVERS_RECEIVED,
    REMOTE_TRACK_ADDED,
    MICROPHONE_LEVEL,
    SESSION_STATE,
    TEARDOWN
}

/** Container describing a diagnostic event emitted by the handset voice stack. */
data class VoiceDiagnosticEvent(
    val type: VoiceDiagnosticType,
    val message: String,
    val metadata: Map<String, Any?> = emptyMap(),
    val timestampMillis: Long = System.currentTimeMillis()
)

/**
 * Collects and exposes diagnostic events so instrumentation, logcat, and live tooling can
 * inspect the handset voice pipeline without attaching debuggers.
 */
@Singleton
class VoiceDiagnosticsReporter @Inject constructor() {

    private val mutex = Mutex()
    private val history = CopyOnWriteArrayList<VoiceDiagnosticEvent>()
    private val eventsFlow = MutableSharedFlow<VoiceDiagnosticEvent>(
        replay = 0,
        extraBufferCapacity = 64
    )

    val events: SharedFlow<VoiceDiagnosticEvent> = eventsFlow.asSharedFlow()

    suspend fun clear() {
        mutex.withLock {
            history.clear()
        }
    }

    fun record(type: VoiceDiagnosticType, message: String, metadata: Map<String, Any?> = emptyMap()) {
        val event = VoiceDiagnosticEvent(type, message, metadata)
        if (!eventsFlow.tryEmit(event)) {
            Log.w(DIAGNOSTIC_TAG, "Dropping diagnostic event due to backpressure: $event")
        }
        history.add(event)
        trimHistory()
        Log.d(
            DIAGNOSTIC_TAG,
            buildString {
                append('[').append(type.name).append("] ").append(message)
                if (metadata.isNotEmpty()) {
                    append(" metadata=").append(metadata)
                }
            }
        )
    }

    fun latest(type: VoiceDiagnosticType): VoiceDiagnosticEvent? {
        return history.lastOrNull { it.type == type }
    }

    fun snapshot(maxEvents: Int = 128): List<VoiceDiagnosticEvent> {
        return history.takeLast(maxEvents)
    }

    private fun trimHistory(maxEntries: Int = 512) {
        if (history.size <= maxEntries) {
            return
        }
        val overflow = history.size - maxEntries
        repeat(overflow) {
            if (history.isEmpty()) return
            history.removeAt(0)
        }
    }
}
