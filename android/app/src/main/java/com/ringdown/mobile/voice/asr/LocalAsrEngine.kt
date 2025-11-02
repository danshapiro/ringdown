package com.ringdown.mobile.voice.asr

import kotlinx.coroutines.flow.SharedFlow

interface LocalAsrEngine {
    val events: SharedFlow<AsrEvent>
    suspend fun start()
    suspend fun stop()
}

sealed class AsrEvent {
    data class Partial(val utteranceId: String, val text: String) : AsrEvent()
    data class Final(val utteranceId: String, val text: String) : AsrEvent()
    data class Error(val throwable: Throwable) : AsrEvent()
}
