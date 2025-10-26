package com.ringdown.mobile.ui

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.util.MainDispatcherRule
import com.ringdown.mobile.data.DeviceDescriptor
import com.ringdown.mobile.data.RegistrationGateway
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.voice.VoiceConnectionState
import com.ringdown.mobile.voice.VoiceSessionGateway
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class MainViewModelVoiceTest {

    @get:Rule
    val dispatcherRule = MainDispatcherRule()

    private val registrationGateway = FakeRegistrationGateway()
    private val voiceGateway = FakeVoiceSessionGateway()

    @Test
    fun `voice state updates propagate to ui state`() = runTest {
        val viewModel = MainViewModel(registrationGateway, voiceGateway)
        voiceGateway.emit(VoiceConnectionState.Connecting)
        advanceUntilIdle()

        val state = viewModel.state.value
        assertThat(state.voiceState).isInstanceOf(VoiceConnectionState.Connecting::class.java)
    }

    @Test
    fun `voice failure surfaces error`() = runTest {
        val viewModel = MainViewModel(registrationGateway, voiceGateway)
        voiceGateway.emit(VoiceConnectionState.Failed("failure"))
        advanceUntilIdle()

        val state = viewModel.state.value
        assertThat(state.errorMessage).isEqualTo("failure")
        assertThat(state.voiceState).isInstanceOf(VoiceConnectionState.Idle::class.java)
    }

    private class FakeRegistrationGateway : RegistrationGateway {
        override suspend fun ensureDeviceId(): String = "device-test"

        override suspend fun register(deviceId: String, descriptor: DeviceDescriptor): RegistrationStatus {
            return RegistrationStatus.Approved(agentName = "agent-a", message = "ok")
        }

        override suspend fun lastKnownAgent(): String? = "agent-a"
    }

    private class FakeVoiceSessionGateway : VoiceSessionGateway {
        private val _state = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Idle)
        override val state: StateFlow<VoiceConnectionState> = _state

        override fun start(deviceId: String, agent: String?) {
            _state.value = VoiceConnectionState.Connecting
        }

        override fun stop() {
            _state.value = VoiceConnectionState.Idle
        }

        fun emit(value: VoiceConnectionState) {
            _state.value = value
        }
    }
}
