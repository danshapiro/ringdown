package com.ringdown.mobile.ui

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.DeviceDescriptor
import com.ringdown.mobile.data.RegistrationGateway
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.util.MainDispatcherRule
import java.util.ArrayDeque
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class MainViewModelTest {

    @get:Rule
    val dispatcherRule = MainDispatcherRule()

    @Test
    fun pendingStatusAutoRetriesAndApproves() = runTest {
        val statuses = ArrayDeque<RegistrationStatus>().apply {
            add(
                RegistrationStatus.Pending(
                    message = "Waiting on approval",
                    pollAfterSeconds = 3,
                ),
            )
            add(
                RegistrationStatus.Approved(
                    agentName = "tester",
                    message = "Approved!",
                ),
            )
        }
        val gateway = FakeRegistrationGateway(statuses)

        val viewModel = MainViewModel(gateway)

        advanceUntilIdle()

        val state = viewModel.state.value
        assertThat(state.deviceId).isNotEmpty()
        assertThat(state.registrationStatus).isInstanceOf(RegistrationStatus.Approved::class.java)
        val approved = state.registrationStatus as RegistrationStatus.Approved
        assertThat(approved.agentName).isEqualTo("tester")
        assertThat(gateway.registerCalls).isEqualTo(2)
    }

    private class FakeRegistrationGateway(
        private val statuses: ArrayDeque<RegistrationStatus>,
    ) : RegistrationGateway {
        var registerCalls: Int = 0

        override suspend fun ensureDeviceId(): String = "device-123"

        override suspend fun register(
            deviceId: String,
            descriptor: DeviceDescriptor,
        ): RegistrationStatus {
            registerCalls += 1
            return if (statuses.isEmpty()) {
                RegistrationStatus.Approved(agentName = "tester", message = "fallback")
            } else {
                statuses.removeFirst()
            }
        }

        override suspend fun lastKnownAgent(): String? = null
    }
}
