package com.ringdown.ui

import com.ringdown.domain.model.DeviceRegistration
import com.ringdown.domain.model.RegistrationStatus
import com.ringdown.domain.model.VoiceSessionState
import com.ringdown.domain.usecase.RegistrationStatusRefresher
import com.ringdown.domain.usecase.VoiceSessionCommands
import com.ringdown.domain.usecase.VoiceSessionController
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
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

            val voiceController = FakeVoiceSessionController()
            val voiceCommands = FakeVoiceSessionCommands()
            val viewModel = MainViewModel(refresher, voiceController, voiceCommands)

            runCurrent()
            val pending = viewModel.state.value
            assertTrue(pending is AppViewState.PendingApproval)
            pending as AppViewState.PendingApproval
            assertEquals(1, pending.attempts)
            assertEquals(2L, pending.nextPollInSeconds)

            advanceTimeBy(2000)
            runCurrent()

            val voiceState = viewModel.state.value
            assertTrue(voiceState is AppViewState.Idle)
            assertEquals("device-123", voiceCommands.lastStartedDevice)
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

            val voiceController = FakeVoiceSessionController()
            val voiceCommands = FakeVoiceSessionCommands()
            val viewModel = MainViewModel(refresher, voiceController, voiceCommands)

            runCurrent()
            viewModel.onPermissionDenied()
            runCurrent()

            val state = viewModel.state.value
            assertTrue(state is AppViewState.Idle)
            state as AppViewState.Idle
            assertEquals("Microphone permission required", state.statusMessage)
            assertEquals(1, voiceCommands.hangUpCount)
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

    private class FakeVoiceSessionController : VoiceSessionController {
        private val _state = MutableStateFlow<VoiceSessionState>(VoiceSessionState.Disconnected)
        override val state: StateFlow<VoiceSessionState> = _state

        override fun startSession() {
            _state.value = VoiceSessionState.Connecting
        }

        override fun hangUp() {
            _state.value = VoiceSessionState.Disconnected
        }
    }

    private class FakeVoiceSessionCommands : VoiceSessionCommands {
        var lastStartedDevice: String? = null
        var hangUpCount: Int = 0

        override fun start(deviceId: String) {
            lastStartedDevice = deviceId
        }

        override fun hangUp() {
            hangUpCount += 1
        }
    }
}

