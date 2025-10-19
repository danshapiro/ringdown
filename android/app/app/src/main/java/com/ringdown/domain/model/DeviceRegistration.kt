package com.ringdown.domain.model

data class DeviceRegistration(
    val deviceId: String,
    val status: RegistrationStatus
)

sealed interface RegistrationStatus {
    data object Pending : RegistrationStatus
    data object Approved : RegistrationStatus
    data class Denied(val reason: String) : RegistrationStatus
    data class Error(val message: String) : RegistrationStatus
}
