package com.ringdown.mobile.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ringdown.mobile.data.DeviceDescriptor
import com.ringdown.mobile.data.RegistrationException
import com.ringdown.mobile.data.RegistrationGateway
import com.ringdown.mobile.data.RegistrationRepository
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.voice.VoiceConnectionState
import com.ringdown.mobile.voice.VoiceSessionGateway
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class MainUiState(
    val deviceId: String = "",
    val isLoading: Boolean = true,
    val registrationStatus: RegistrationStatus? = null,
    val errorMessage: String? = null,
    val showMicrophoneReminder: Boolean = false,
    val microphonePermissionGranted: Boolean = false,
    val lastApprovedAgent: String? = null,
    val pendingAutoConnect: Boolean = false,
    val permissionRequestVersion: Int = 0,
    val voiceState: VoiceConnectionState = VoiceConnectionState.Idle,
)

@HiltViewModel
class MainViewModel @Inject constructor(
    private val registrationGateway: RegistrationGateway,
    private val voiceGateway: VoiceSessionGateway,
) : ViewModel() {

    private val _state = MutableStateFlow(MainUiState())
    val state = _state.asStateFlow()

    private var pollJob: Job? = null
    private var lastDescriptor: DeviceDescriptor = RegistrationRepository.buildDefaultDescriptor()

    init {
        viewModelScope.launch {
            initialise()
        }
        viewModelScope.launch {
            voiceGateway.state.collect { voiceState ->
                _state.update { current ->
                    when (voiceState) {
                        is VoiceConnectionState.Failed -> current.copy(
                            voiceState = VoiceConnectionState.Idle,
                            errorMessage = voiceState.reason,
                        )

                        else -> current.copy(voiceState = voiceState)
                    }
                }
            }
        }
    }

    private suspend fun initialise() {
        val deviceId = registrationGateway.ensureDeviceId()
        val agent = registrationGateway.lastKnownAgent()
        _state.update {
            it.copy(
                deviceId = deviceId,
                lastApprovedAgent = agent,
                isLoading = true,
                errorMessage = null,
            )
        }
        refreshRegistration()
    }

    fun onCheckAgainClicked() {
        viewModelScope.launch {
            refreshRegistration(manual = true)
        }
    }

    fun onPermissionResult(granted: Boolean, fromUserAction: Boolean = true) {
        _state.update {
            it.copy(
                microphonePermissionGranted = granted,
                showMicrophoneReminder = if (fromUserAction) !granted else it.showMicrophoneReminder && !granted,
                pendingAutoConnect = if (granted) it.pendingAutoConnect else false,
            )
        }
        if (granted) {
            maybeStartVoiceSession()
        }
    }

    fun acknowledgeError() {
        _state.update { it.copy(errorMessage = null) }
    }

    private suspend fun refreshRegistration(manual: Boolean = false) {
        val deviceId = _state.value.deviceId
        if (deviceId.isBlank()) return

        if (manual) {
            pollJob?.cancel()
        }

        _state.update { it.copy(isLoading = true, errorMessage = null) }
        try {
            val status = registrationGateway.register(deviceId, lastDescriptor)
            _state.update {
                val previousStatus = it.registrationStatus
                val updatedAgent = when (status) {
                    is RegistrationStatus.Approved -> status.agentName ?: it.lastApprovedAgent
                    else -> it.lastApprovedAgent
                }
                val shouldAutoConnect = when (status) {
                    is RegistrationStatus.Approved -> if (previousStatus is RegistrationStatus.Approved) {
                        it.pendingAutoConnect
                    } else {
                        true
                    }
                    else -> false
                }
                it.copy(
                    isLoading = false,
                    registrationStatus = status,
                    lastApprovedAgent = updatedAgent,
                    pendingAutoConnect = shouldAutoConnect,
                )
            }
            maybeStartVoiceSession()
            schedulePollIfNeeded(status)
        } catch (error: Exception) {
            val message = when (error) {
                is RegistrationException -> error.message ?: "Registration failed."
                else -> "Unable to talk to the backend. Check your connection."
            }
            _state.update {
                it.copy(
                    isLoading = false,
                    errorMessage = message,
                )
            }
        }
    }

    private fun schedulePollIfNeeded(status: RegistrationStatus) {
        pollJob?.cancel()
        if (status is RegistrationStatus.Pending) {
            val delaySeconds = (status.pollAfterSeconds ?: DEFAULT_POLL_SECONDS).coerceIn(3, 30)
            pollJob = viewModelScope.launch {
                delay(delaySeconds * 1_000L)
                refreshRegistration()
            }
        }
    }

    fun startVoiceSession() {
        val current = _state.value
        val deviceId = current.deviceId
        if (deviceId.isBlank()) {
            return
        }
        val status = current.registrationStatus
        if (status !is RegistrationStatus.Approved) {
            _state.update {
                it.copy(errorMessage = "Device pending approval.")
            }
            return
        }

        if (!current.microphonePermissionGranted) {
            _state.update {
                it.copy(
                    showMicrophoneReminder = true,
                    pendingAutoConnect = true,
                    permissionRequestVersion = it.permissionRequestVersion + 1,
                )
            }
            return
        }

        val agent = status.agentName ?: current.lastApprovedAgent
        _state.update {
            it.copy(
                showMicrophoneReminder = false,
                pendingAutoConnect = false,
                permissionRequestVersion = it.permissionRequestVersion,
            )
        }
        voiceGateway.start(deviceId, agent)
    }

    fun stopVoiceSession() {
        voiceGateway.stop()
    }

    private fun maybeStartVoiceSession() {
        val current = _state.value
        val status = current.registrationStatus
        if (!current.pendingAutoConnect) return
        if (status !is RegistrationStatus.Approved) return
        startVoiceSession()
    }

    companion object {
        private const val DEFAULT_POLL_SECONDS = 5
    }

    override fun onCleared() {
        super.onCleared()
        voiceGateway.stop()
    }
}
