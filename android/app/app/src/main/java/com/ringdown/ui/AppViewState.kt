package com.ringdown.ui

sealed interface AppViewState {
    data object Loading : AppViewState
    data class PendingApproval(
        val deviceId: String,
        val attempts: Int,
        val nextPollInSeconds: Long?
    ) : AppViewState

    data class Idle(val statusMessage: String? = null) : AppViewState
    data class Error(val message: String) : AppViewState
}
