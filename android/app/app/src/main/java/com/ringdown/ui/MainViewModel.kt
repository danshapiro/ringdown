package com.ringdown.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ringdown.domain.model.DeviceRegistration
import com.ringdown.domain.model.RegistrationStatus
import com.ringdown.domain.model.VoiceSessionState
import com.ringdown.domain.usecase.RegistrationStatusRefresher
import com.ringdown.domain.usecase.VoiceSessionCommands
import com.ringdown.domain.usecase.VoiceSessionController
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

@HiltViewModel
class MainViewModel @Inject constructor(
    private val refreshRegistrationStatus: RegistrationStatusRefresher,
    private val voiceSessionController: VoiceSessionController,
    private val voiceSessionCommands: VoiceSessionCommands
) : ViewModel() {

    private sealed interface RegistrationUiState {
        data object Loading : RegistrationUiState
        data class Pending(
            val deviceId: String,
            val attempts: Int,
            val nextPollInSeconds: Long?
        ) : RegistrationUiState

        data class Approved(
            val deviceId: String,
            val statusMessage: String? = null
        ) : RegistrationUiState

        data class Denied(val message: String) : RegistrationUiState
        data class Failed(val message: String) : RegistrationUiState
    }

    private val registrationState = MutableStateFlow<RegistrationUiState>(RegistrationUiState.Loading)
    private val idleStatusMessage = MutableStateFlow<String?>(null)

    private var attempts = 0
    private var autoRefreshJob: Job? = null
    private var lastDeviceId: String? = null

    val state: StateFlow<AppViewState> = combine(
        registrationState,
        voiceSessionController.state,
        idleStatusMessage
    ) { registration, voice, message ->
        when (registration) {
            is RegistrationUiState.Loading -> AppViewState.Loading
            is RegistrationUiState.Pending -> AppViewState.PendingApproval(
                deviceId = registration.deviceId,
                attempts = registration.attempts,
                nextPollInSeconds = registration.nextPollInSeconds
            )

            is RegistrationUiState.Denied -> AppViewState.Error(registration.message)
            is RegistrationUiState.Failed -> AppViewState.Error(registration.message)
            is RegistrationUiState.Approved -> {
                when (voice) {
                    VoiceSessionState.Connecting -> AppViewState.VoiceConnecting(registration.deviceId)
                    is VoiceSessionState.Reconnecting -> AppViewState.VoiceConnecting(registration.deviceId)
                    is VoiceSessionState.Active -> AppViewState.VoiceActive(
                        deviceId = registration.deviceId,
                        connectedSinceEpochMillis = voice.connectedSince.toEpochMilli()
                    )

                    is VoiceSessionState.Error -> AppViewState.Error(voice.message)
                    else -> AppViewState.Idle(statusMessage = registration.statusMessage ?: message)
                }
            }
        }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, AppViewState.Loading)

    init {
        triggerRefresh(connectIfApproved = true)
        viewModelScope.launch {
            voiceSessionController.state.collect { voiceState ->
                if (voiceState is VoiceSessionState.Error) {
                    idleStatusMessage.value = voiceState.message
                }
                if (voiceState is VoiceSessionState.Disconnected) {
                    idleStatusMessage.value = null
                }
            }
        }
    }

    fun onCheckAgain() {
        triggerRefresh(connectIfApproved = true)
    }

    fun onReconnectRequested() {
        triggerRefresh(connectIfApproved = true)
    }

    fun onHangUpRequested() {
        voiceSessionCommands.hangUp()
    }

    fun onPermissionDenied() {
        idleStatusMessage.value = "Microphone permission required"
        voiceSessionCommands.hangUp()
    }

    private fun triggerRefresh(connectIfApproved: Boolean) {
        autoRefreshJob?.cancel()
        viewModelScope.launch {
            val registration = refreshRegistrationStatus()
            handleRegistrationResult(registration, connectIfApproved)
        }
    }

    private fun handleRegistrationResult(
        registration: DeviceRegistration,
        connectIfApproved: Boolean
    ) {
        when (val status = registration.status) {
            RegistrationStatus.Pending -> {
                attempts += 1
                lastDeviceId = registration.deviceId
                registrationState.value = RegistrationUiState.Pending(
                    deviceId = registration.deviceId,
                    attempts = attempts,
                    nextPollInSeconds = registration.pollAfterSeconds
                )
                scheduleAutoRefresh(registration.pollAfterSeconds, connectIfApproved)
            }

            RegistrationStatus.Approved -> {
                attempts = 0
                autoRefreshJob?.cancel()
                lastDeviceId = registration.deviceId
                registrationState.value = RegistrationUiState.Approved(
                    deviceId = registration.deviceId,
                    statusMessage = idleStatusMessage.value
                )
                if (connectIfApproved) {
                    startVoiceSessionInternal(registration.deviceId)
                }
            }

            is RegistrationStatus.Denied -> {
                attempts = 0
                autoRefreshJob?.cancel()
                registrationState.value = RegistrationUiState.Denied(status.reason)
            }

            is RegistrationStatus.Error -> {
                attempts = 0
                autoRefreshJob?.cancel()
                registrationState.value = RegistrationUiState.Failed(status.message)
            }
        }
    }

    private fun scheduleAutoRefresh(pollAfterSeconds: Long?, connectIfApproved: Boolean) {
        if (pollAfterSeconds == null || pollAfterSeconds <= 0L) {
            return
        }

        autoRefreshJob = viewModelScope.launch {
            delay(pollAfterSeconds * 1000)
            val registration = refreshRegistrationStatus()
            handleRegistrationResult(registration, connectIfApproved)
        }
    }

    private fun startVoiceSession(deviceId: String) {
        idleStatusMessage.value = null
        startVoiceSessionInternal(deviceId)
    }

    private fun startVoiceSessionInternal(deviceId: String) {
        val currentVoiceState = voiceSessionController.state.value
        if (currentVoiceState is VoiceSessionState.Connecting || currentVoiceState is VoiceSessionState.Active) {
            return
        }
        idleStatusMessage.value = null
        voiceSessionCommands.start(deviceId)
    }
}
