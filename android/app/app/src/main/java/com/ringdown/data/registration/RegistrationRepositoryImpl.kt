package com.ringdown.data.registration

import com.ringdown.di.IoDispatcher
import com.ringdown.domain.model.DeviceRegistration
import com.ringdown.domain.model.RegistrationStatus
import javax.inject.Inject
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.withContext

interface RegistrationRepository {
    suspend fun checkRegistration(deviceId: String): DeviceRegistration
}

class RegistrationRepositoryImpl @Inject constructor(
    private val api: RegistrationApi,
    @IoDispatcher private val ioDispatcher: CoroutineDispatcher
) : RegistrationRepository {

    override suspend fun checkRegistration(deviceId: String): DeviceRegistration =
        withContext(ioDispatcher) {
            val response = api.register(RegistrationRequest(deviceId = deviceId))
            DeviceRegistration(
                deviceId = deviceId,
                status = response.toDomainStatus(),
                pollAfterSeconds = response.pollAfterSeconds
            )
        }

    private fun RegistrationResponse.toDomainStatus(): RegistrationStatus = when (status) {
        RegistrationStatusDto.PENDING -> RegistrationStatus.Pending
        RegistrationStatusDto.APPROVED -> RegistrationStatus.Approved
        RegistrationStatusDto.DENIED -> RegistrationStatus.Denied(message ?: "Device disabled")
    }
}
