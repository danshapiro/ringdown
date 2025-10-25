package com.ringdown.mobile

import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.ui.MainViewModel
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import androidx.lifecycle.ViewModelProvider

@RunWith(AndroidJUnit4::class)
class ConnectedVoiceMvpAndroidTest {

    @get:Rule
    val composeTestRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun registersDeviceWithStubBackend() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assertThat(context).isNotNull()

        composeTestRule.waitUntil(timeoutMillis = 10_000) {
            val state = mainViewModel().state.value
            state.deviceId.isNotBlank() && !state.isLoading
        }

        val finalState = mainViewModel().state.value

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

    private fun mainViewModel(): MainViewModel {
        return ViewModelProvider(composeTestRule.activity)[MainViewModel::class.java]
    }
}
