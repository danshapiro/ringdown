package com.ringdown.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ringdown.domain.model.RegistrationStatus
import com.ringdown.domain.usecase.RegistrationStatusRefresher
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

@HiltViewModel
class MainViewModel @Inject constructor(
    private val refreshRegistrationStatus: RegistrationStatusRefresher
) : ViewModel() {

    private val _state = MutableStateFlow<AppViewState>(AppViewState.Loading)
    val state: StateFlow<AppViewState> = _state.asStateFlow()

    private var attempts = 0
    private var autoRefreshJob: Job? = null

    init {
        triggerRefresh()
    }

    fun onCheckAgain() {
        triggerRefresh()
    }

    fun onReconnectRequested() {
        triggerRefresh()
    }

    fun onPermissionDenied() {
        _state.value = AppViewState.Idle(statusMessage = "Microphone permission required")
    }

    private fun triggerRefresh() {
        autoRefreshJob?.cancel()
        viewModelScope.launch {
            performRefresh()
        }
    }

    private suspend fun performRefresh() {
        val registration = refreshRegistrationStatus()
        when (val status = registration.status) {
            RegistrationStatus.Pending -> {
                attempts += 1
                _state.value = AppViewState.PendingApproval(
                    deviceId = registration.deviceId,
                    attempts = attempts,
                    nextPollInSeconds = registration.pollAfterSeconds
                )
                scheduleAutoRefresh(registration.pollAfterSeconds)
            }

            RegistrationStatus.Approved -> {
                attempts = 0
                autoRefreshJob?.cancel()
                _state.value = AppViewState.Idle()
            }

            is RegistrationStatus.Denied -> {
                attempts = 0
                autoRefreshJob?.cancel()
                _state.value = AppViewState.Error(status.reason)
            }

            is RegistrationStatus.Error -> {
                attempts = 0
                autoRefreshJob?.cancel()
                _state.value = AppViewState.Error(status.message)
            }
        }
    }

    private fun scheduleAutoRefresh(pollAfterSeconds: Long?) {
        if (pollAfterSeconds == null || pollAfterSeconds <= 0L) {
            return
        }

        autoRefreshJob = viewModelScope.launch {
            delay(pollAfterSeconds * 1000)
            performRefresh()
        }
    }
}
