package com.ringdown.mobile.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ringdown.mobile.data.DeviceDescriptor
import com.ringdown.mobile.data.RegistrationException
import com.ringdown.mobile.data.RegistrationGateway
import com.ringdown.mobile.data.RegistrationRepository
import com.ringdown.mobile.domain.RegistrationStatus
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
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
)

@HiltViewModel
class MainViewModel @Inject constructor(
    private val registrationGateway: RegistrationGateway,
) : ViewModel() {

    private val _state = MutableStateFlow(MainUiState())
    val state = _state.asStateFlow()

    private var pollJob: Job? = null
    private var lastDescriptor: DeviceDescriptor = RegistrationRepository.buildDefaultDescriptor()

    init {
        viewModelScope.launch {
            initialise()
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
            )
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
                it.copy(
                    isLoading = false,
                    registrationStatus = status,
                )
            }
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

    companion object {
        private const val DEFAULT_POLL_SECONDS = 5
    }
}
