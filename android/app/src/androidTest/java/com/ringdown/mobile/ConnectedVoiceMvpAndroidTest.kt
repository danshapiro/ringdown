package com.ringdown.mobile

import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.testing.RuntimePermissionRule
import com.ringdown.mobile.voice.VoiceConnectionState
import dagger.hilt.android.testing.HiltAndroidRule
import dagger.hilt.android.testing.HiltAndroidTest
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TestRule
import org.junit.runner.RunWith

@HiltAndroidTest
@RunWith(AndroidJUnit4::class)
class ConnectedVoiceMvpAndroidTest {

    @get:Rule(order = 0)
    val hiltRule = HiltAndroidRule(this)

    @Before
    fun setUp() {
        hiltRule.inject()
    }

    @Test
    fun registersDeviceWithStubBackend() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            val finalState = scenario.awaitRegisteredState()

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
}

@HiltAndroidTest
@RunWith(AndroidJUnit4::class)
class ConnectedVoiceMvpAutoConnectAndroidTest {

    @get:Rule(order = 0)
    val hiltRule = HiltAndroidRule(this)

    @get:Rule(order = 1)
    val microphoneRule: TestRule = RuntimePermissionRule.microphoneGranted()

    @Before
    fun setUp() {
        hiltRule.inject()
    }

    @Test
    fun autoConnectsWhenPermissionPreGranted() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            val finalState = scenario.awaitRegisteredState()

            assertThat(finalState.deviceId).isNotEmpty()
            assertThat(finalState.registrationStatus).isInstanceOf(RegistrationStatus.Approved::class.java)
            assertThat(finalState.microphonePermissionGranted).isTrue()

            val voiceState = scenario.awaitVoiceState { state ->
                state !is VoiceConnectionState.Idle ||
                    scenario.withMainViewModel { !it.state.value.pendingAutoConnect }
            }

            when (voiceState) {
                is VoiceConnectionState.Connecting -> Unit
                is VoiceConnectionState.Connected -> Unit
                is VoiceConnectionState.Failed -> error("Voice session failed unexpectedly: ${voiceState.reason}")
                VoiceConnectionState.Idle -> {
                    val pendingCleared = scenario.withMainViewModel { !it.state.value.pendingAutoConnect }
                    assertThat(pendingCleared).isTrue()
                }
            }

            scenario.withMainViewModel { it.stopVoiceSession() }
            scenario.awaitVoiceState { state -> state is VoiceConnectionState.Idle }
        }
    }
}
