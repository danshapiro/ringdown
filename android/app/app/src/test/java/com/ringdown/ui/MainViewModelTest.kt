package com.ringdown.ui

import com.ringdown.domain.model.DeviceRegistration
import com.ringdown.domain.model.RegistrationStatus
import com.ringdown.domain.usecase.RegistrationStatusRefresher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class MainViewModelTest {

    @Test
    fun pendingRegistrationSchedulesNextPoll() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        Dispatchers.setMain(dispatcher)
        try {
            val refresher = FakeRegistrationStatusRefresher().apply {
                enqueue(
                    DeviceRegistration(
                        deviceId = "device-123",
                        status = RegistrationStatus.Pending,
                        pollAfterSeconds = 2
                    )
                )
                enqueue(
                    DeviceRegistration(
                        deviceId = "device-123",
                        status = RegistrationStatus.Approved,
                        pollAfterSeconds = null
                    )
                )
            }

            val viewModel = MainViewModel(refresher)

            advanceUntilIdle()
            val pending = viewModel.state.value
            assertTrue(pending is AppViewState.PendingApproval)
            pending as AppViewState.PendingApproval
            assertEquals(1, pending.attempts)
            assertEquals(2L, pending.nextPollInSeconds)

            advanceTimeBy(2000)
            advanceUntilIdle()

            assertTrue(viewModel.state.value is AppViewState.Idle)
        } finally {
            Dispatchers.resetMain()
        }
    }

    @Test
    fun permissionDeniedSetsIdleMessage() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        Dispatchers.setMain(dispatcher)
        try {
            val refresher = FakeRegistrationStatusRefresher().apply {
                enqueue(
                    DeviceRegistration(
                        deviceId = "device-456",
                        status = RegistrationStatus.Approved,
                        pollAfterSeconds = null
                    )
                )
            }

            val viewModel = MainViewModel(refresher)

            advanceUntilIdle()
            viewModel.onPermissionDenied()

            val state = viewModel.state.value
            assertTrue(state is AppViewState.Idle)
            state as AppViewState.Idle
            assertEquals("Microphone permission required", state.statusMessage)
        } finally {
            Dispatchers.resetMain()
        }
    }

    private class FakeRegistrationStatusRefresher : RegistrationStatusRefresher {
        private val responses = ArrayDeque<DeviceRegistration>()

        fun enqueue(registration: DeviceRegistration) {
            responses.addLast(registration)
        }

        override suspend fun invoke(): DeviceRegistration {
            if (responses.isEmpty()) {
                error("No queued registration response for test.")
            }
            return responses.removeFirst()
        }
    }
}
