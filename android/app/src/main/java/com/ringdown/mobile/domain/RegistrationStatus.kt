package com.ringdown.mobile.domain

sealed interface RegistrationStatus {
    data class Pending(
        val message: String,
        val pollAfterSeconds: Int?,
    ) : RegistrationStatus

    data class Approved(
        val agentName: String?,
        val message: String?,
    ) : RegistrationStatus

    data class Denied(
        val message: String,
    ) : RegistrationStatus
}
