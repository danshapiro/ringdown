package com.ringdown.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ringdown.domain.model.RegistrationStatus
import com.ringdown.domain.usecase.RefreshRegistrationStatusUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

@HiltViewModel
class MainViewModel @Inject constructor(
    private val refreshRegistrationStatus: RefreshRegistrationStatusUseCase
) : ViewModel() {

    private val _state = MutableStateFlow<AppViewState>(AppViewState.Loading)
    val state: StateFlow<AppViewState> = _state.asStateFlow()

    private var attempts = 0

    init {
        refresh()
    }

    fun onCheckAgain() {
        refresh()
    }

    fun onReconnectRequested() {
        refresh()
    }

    private fun refresh() {
        viewModelScope.launch {
            val registration = refreshRegistrationStatus()
            when (val status = registration.status) {
                RegistrationStatus.Pending -> {
                    attempts += 1
                    _state.value = AppViewState.PendingApproval(
                        deviceId = registration.deviceId,
                        attempts = attempts
                    )
                }

                RegistrationStatus.Approved -> {
                    attempts = 0
                    _state.value = AppViewState.Idle
                }

                is RegistrationStatus.Denied -> {
                    _state.value = AppViewState.Error(status.reason)
                }

                is RegistrationStatus.Error -> {
                    _state.value = AppViewState.Error(status.message)
                }
            }
        }
    }
}
