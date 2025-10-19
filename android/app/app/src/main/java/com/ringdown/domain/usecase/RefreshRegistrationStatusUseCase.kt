package com.ringdown.domain.usecase

import com.ringdown.data.device.DeviceIdStorage
import com.ringdown.data.registration.RegistrationRepository
import com.ringdown.domain.model.DeviceRegistration
import com.ringdown.domain.model.RegistrationStatus
import javax.inject.Inject

class RefreshRegistrationStatusUseCase @Inject constructor(
    private val deviceIdStorage: DeviceIdStorage,
    private val registrationRepository: RegistrationRepository
) : RegistrationStatusRefresher {

    override suspend operator fun invoke(): DeviceRegistration {
        val deviceId = runCatching { deviceIdStorage.getOrCreate() }
            .getOrElse { throwable ->
                return DeviceRegistration(
                    deviceId = "",
                    status = RegistrationStatus.Error(throwable.message ?: "Unable to read device id"),
                    pollAfterSeconds = null
                )
            }

        return runCatching {
            registrationRepository.checkRegistration(deviceId)
        }.getOrElse { throwable ->
            DeviceRegistration(
                deviceId = deviceId,
                status = RegistrationStatus.Error(throwable.message ?: "Registration failed"),
                pollAfterSeconds = null
            )
        }
    }
}
