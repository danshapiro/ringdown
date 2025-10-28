package com.ringdown.mobile

import android.Manifest
import android.os.SystemClock
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.ui.MainUiState
import com.ringdown.mobile.ui.MainViewModel
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ConnectedVoiceMvpAndroidTest {

    @get:Rule
    val composeTestRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun registersDeviceWithStubBackend() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        assertThat(context).isNotNull()

        revokeMicrophonePermission(instrumentation, context.packageName)

        val finalState = awaitRegisteredState()

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

    private fun revokeMicrophonePermission(instrumentation: android.app.Instrumentation, packageName: String) {
        try {
            instrumentation.uiAutomation.revokeRuntimePermission(packageName, Manifest.permission.RECORD_AUDIO)
        } catch (_: SecurityException) {
            // Device API level or permission state may prevent revocation; that's fine.
        } catch (_: IllegalArgumentException) {
            // Package name or permission missing; safe to ignore for instrumentation.
        }
    }

    private fun <T> withMainViewModel(block: (MainViewModel) -> T): T {
        var result: T? = null
        InstrumentationRegistry.getInstrumentation().runOnMainSync {
            val activity = composeTestRule.activity
            val viewModel = ViewModelProvider(activity)[MainViewModel::class.java]
            result = block(viewModel)
        }
        @Suppress("UNCHECKED_CAST")
        return result as T
    }

    private fun awaitRegisteredState(timeoutMillis: Long = 10_000L, pollIntervalMillis: Long = 100L): MainUiState {
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
}
