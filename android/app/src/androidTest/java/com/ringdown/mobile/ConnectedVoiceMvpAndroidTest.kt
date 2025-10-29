package com.ringdown.mobile

import android.os.SystemClock
import androidx.compose.ui.test.junit4.AndroidComposeTestRule
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.ext.junit.rules.ActivityScenarioRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.testing.RuntimePermissionRule
import com.ringdown.mobile.ui.MainUiState
import com.ringdown.mobile.ui.MainViewModel
import com.ringdown.mobile.voice.VoiceConnectionState
import org.junit.Rule
import org.junit.Test
import org.junit.rules.RuleChain
import org.junit.rules.TestRule
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ConnectedVoiceMvpAndroidTest {

    private val composeTestRule = createAndroidComposeRule<MainActivity>()

    /**
     * Voice sessions auto start with the last granted microphone permission.
     * The rule clears RECORD_AUDIO before MainActivity launches and restores
     * the original grant afterward so repeated runs stay isolated.
     */
    @get:Rule
    val ruleChain: TestRule = RuleChain
        .outerRule(RuntimePermissionRule.microphone())
        .around(composeTestRule)

    @Test
    fun registersDeviceWithStubBackend() {
        val finalState = composeTestRule.awaitRegisteredState()

        assertThat(finalState.deviceId).isNotEmpty()
        assertThat(finalState.errorMessage).isNull()

        val status = finalState.registrationStatus
        assertThat(status).isNotNull()

        when (status) {
            is RegistrationStatus.Pending -> {
                assertThat(status.message).isNotEmpty()
                assertThat(status.pollAfterSeconds).isNotNull()
            }
            is RegistrationStatus.Approved -> {
                assertThat(status.agentName).isNotEmpty()
                assertThat(status.message).isNotEmpty()
            }
            is RegistrationStatus.Denied, null -> error("Unexpected registration status: $status")
        }
    }
}

@RunWith(AndroidJUnit4::class)
class ConnectedVoiceMvpAutoConnectAndroidTest {

    private val composeTestRule = createAndroidComposeRule<MainActivity>()

    /**
     * Covers the path where microphone access is already granted so the UI proceeds
     * straight into the voice session handshake.
     */
    @get:Rule
    val ruleChain: TestRule = RuleChain
        .outerRule(RuntimePermissionRule.microphoneGranted())
        .around(composeTestRule)

    @Test
    fun autoConnectsWhenPermissionPreGranted() {
        val finalState = composeTestRule.awaitRegisteredState()

        assertThat(finalState.deviceId).isNotEmpty()
        assertThat(finalState.registrationStatus).isInstanceOf(RegistrationStatus.Approved::class.java)
        assertThat(finalState.microphonePermissionGranted).isTrue()

        val voiceState = composeTestRule.awaitVoiceState { state ->
            state !is VoiceConnectionState.Idle ||
                composeTestRule.withMainViewModel { !it.state.value.pendingAutoConnect }
        }

        when (voiceState) {
            is VoiceConnectionState.Connecting -> Unit
            is VoiceConnectionState.Connected -> Unit
            is VoiceConnectionState.Failed -> error("Voice session failed unexpectedly: ${voiceState.reason}")
            VoiceConnectionState.Idle -> {
                val pendingCleared = composeTestRule.withMainViewModel { !it.state.value.pendingAutoConnect }
                assertThat(pendingCleared).isTrue()
            }
        }

        composeTestRule.withMainViewModel { it.stopVoiceSession() }
        composeTestRule.waitForIdle()
        composeTestRule.awaitVoiceState { state -> state is VoiceConnectionState.Idle }
        composeTestRule.activityRule.scenario.close()
    }
}

private typealias MainActivityRule =
    AndroidComposeTestRule<ActivityScenarioRule<MainActivity>, MainActivity>

private fun <T> MainActivityRule.withMainViewModel(block: (MainViewModel) -> T): T {
    var result: T? = null
    InstrumentationRegistry.getInstrumentation().runOnMainSync {
        val activity = activity
        val viewModel = ViewModelProvider(activity)[MainViewModel::class.java]
        result = block(viewModel)
    }
    @Suppress("UNCHECKED_CAST")
    return result as T
}

private fun MainActivityRule.awaitRegisteredState(
    timeoutMillis: Long = 10_000L,
    pollIntervalMillis: Long = 100L
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

private fun MainActivityRule.awaitVoiceState(
    timeoutMillis: Long = 10_000L,
    pollIntervalMillis: Long = 100L,
    predicate: (VoiceConnectionState) -> Boolean
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
