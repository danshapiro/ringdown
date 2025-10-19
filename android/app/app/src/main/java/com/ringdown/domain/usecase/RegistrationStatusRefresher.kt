package com.ringdown.domain.usecase

import com.ringdown.domain.model.DeviceRegistration

fun interface RegistrationStatusRefresher {
    suspend operator fun invoke(): DeviceRegistration
}
