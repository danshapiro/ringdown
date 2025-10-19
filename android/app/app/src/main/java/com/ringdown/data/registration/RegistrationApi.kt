package com.ringdown.data.registration

import retrofit2.http.Body
import retrofit2.http.POST

interface RegistrationApi {

    @POST("v1/mobile/devices/register")
    suspend fun register(
        @Body request: RegistrationRequest
    ): RegistrationResponse
}

data class RegistrationRequest(
    val deviceId: String
)

data class RegistrationResponse(
    val status: RegistrationStatusDto,
    val message: String? = null,
    val pollAfterSeconds: Long? = null
)

enum class RegistrationStatusDto {
    PENDING,
    APPROVED,
    DENIED
}
