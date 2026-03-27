package com.ringdown.mobile

import android.os.SystemClock
import androidx.lifecycle.ViewModelProvider
import androidx.test.core.app.ActivityScenario
import com.ringdown.mobile.ui.MainUiState
import com.ringdown.mobile.ui.MainViewModel
import com.ringdown.mobile.voice.VoiceConnectionState

fun <T> ActivityScenario<MainActivity>.withMainViewModel(block: (MainViewModel) -> T): T {
    var result: T? = null
    onActivity { activity ->
        val viewModel = ViewModelProvider(activity)[MainViewModel::class.java]
        result = block(viewModel)
    }
    return result!!
}

fun ActivityScenario<MainActivity>.awaitRegisteredState(
    timeoutMillis: Long = 30_000L,
    pollIntervalMillis: Long = 100L,
): MainUiState {
    val deadline = SystemClock.elapsedRealtime() + timeoutMillis
    var latestState: MainUiState? = null

    while (SystemClock.elapsedRealtime() < deadline) {
        val current = withMainViewModel { it.state.value }
        latestState = current
        if (current.deviceId.isNotBlank() && !current.isLoading) {
            return current
        }
        Thread.sleep(pollIntervalMillis)
    }

    error("Timed out waiting for device registration. Last state=$latestState")
}

fun ActivityScenario<MainActivity>.awaitVoiceState(
    timeoutMillis: Long = 30_000L,
    pollIntervalMillis: Long = 100L,
    predicate: (VoiceConnectionState) -> Boolean,
): VoiceConnectionState {
    val deadline = SystemClock.elapsedRealtime() + timeoutMillis
    var latestState: VoiceConnectionState? = null

    while (SystemClock.elapsedRealtime() < deadline) {
        val state = withMainViewModel { it.state.value.voiceState }
        latestState = state
        if (predicate(state)) {
            return state
        }
        Thread.sleep(pollIntervalMillis)
    }

    error("Timed out waiting for voice state. Last value=$latestState")
}
