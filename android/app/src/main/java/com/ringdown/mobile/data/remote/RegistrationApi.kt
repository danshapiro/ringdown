package com.ringdown.mobile.data.remote

import com.squareup.moshi.Json
import retrofit2.http.Body
import retrofit2.http.POST

interface RegistrationApi {
    @POST("v1/mobile/devices/register")
    suspend fun registerDevice(@Body payload: RegisterDeviceRequest): RegisterDeviceResponse
}

data class RegisterDeviceRequest(
    @Json(name = "deviceId") val deviceId: String,
    @Json(name = "label") val label: String?,
    @Json(name = "platform") val platform: String?,
    @Json(name = "model") val model: String?,
    @Json(name = "appVersion") val appVersion: String?,
)

data class RegisterDeviceResponse(
    @Json(name = "status") val status: String,
    @Json(name = "message") val message: String?,
    @Json(name = "pollAfterSeconds") val pollAfterSeconds: Int?,
    @Json(name = "agent") val agent: String?,
)
