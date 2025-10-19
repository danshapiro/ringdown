package com.ringdown.ui

sealed interface AppViewState {
    data object Loading : AppViewState
    data class PendingApproval(
        val deviceId: String,
        val attempts: Int
    ) : AppViewState

    data object Idle : AppViewState
    data class Error(val message: String) : AppViewState
}
