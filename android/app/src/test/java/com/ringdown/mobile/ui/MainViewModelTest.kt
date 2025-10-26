package com.ringdown.mobile.ui

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.DeviceDescriptor
import com.ringdown.mobile.data.RegistrationGateway
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.util.MainDispatcherRule
import com.ringdown.mobile.voice.VoiceConnectionState
import com.ringdown.mobile.voice.VoiceSessionGateway
import java.util.ArrayDeque
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.test.advanceTimeBy
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

        val voiceGateway = RecordingVoiceGateway()
        val viewModel = MainViewModel(gateway, voiceGateway)

        viewModel.onPermissionResult(true)

        advanceUntilIdle()

        advanceTimeBy(3_000)
        advanceUntilIdle()

        val state = viewModel.state.value
        assertThat(state.deviceId).isNotEmpty()
        assertThat(state.registrationStatus).isInstanceOf(RegistrationStatus.Approved::class.java)
        val approved = state.registrationStatus as RegistrationStatus.Approved
        assertThat(approved.agentName).isEqualTo("tester")
        assertThat(gateway.registerCalls).isEqualTo(2)
        assertThat(voiceGateway.starts).containsExactly("device-123" to "tester")
        assertThat(state.pendingAutoConnect).isFalse()
        assertThat(state.permissionRequestVersion).isEqualTo(0)
    }

    @Test
    fun autoConnectsAfterApprovalRequestsPermissionWhenDenied() = runTest {
        val gateway = FakeRegistrationGateway(
            ArrayDeque(
                listOf(
                    RegistrationStatus.Pending(
                        message = "Waiting",
                        pollAfterSeconds = 2,
                    ),
                    RegistrationStatus.Approved(
                        agentName = "tester",
                        message = "Approved!",
                    ),
                ),
            ),
        )
        val voiceGateway = RecordingVoiceGateway()
        val viewModel = MainViewModel(gateway, voiceGateway)

        viewModel.onPermissionResult(false)

        advanceUntilIdle()
        advanceTimeBy(2_000)
        advanceUntilIdle()

        viewModel.onPermissionResult(false)
        advanceUntilIdle()

        val state = viewModel.state.value
        assertThat(state.pendingAutoConnect).isFalse()
        assertThat(state.showMicrophoneReminder).isTrue()
        assertThat(voiceGateway.starts).isEmpty()
        assertThat(state.permissionRequestVersion).isGreaterThan(0)
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

    private class RecordingVoiceGateway : VoiceSessionGateway {
        private val _state = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Idle)
        override val state: StateFlow<VoiceConnectionState> = _state

        val starts = mutableListOf<Pair<String, String?>>()

        override fun start(deviceId: String, agent: String?) {
            starts += deviceId to agent
        }

        override fun stop() {}
    }
}
