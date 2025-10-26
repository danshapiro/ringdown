package com.ringdown.mobile.data.remote

import com.squareup.moshi.Json
import retrofit2.http.Body
import retrofit2.http.POST

interface VoiceApi {
    @POST("v1/mobile/voice/session")
    suspend fun createVoiceSession(@Body payload: VoiceSessionRequest): VoiceSessionResponse
}

data class VoiceSessionRequest(
    @Json(name = "deviceId") val deviceId: String,
    @Json(name = "agent") val agent: String?,
)

data class VoiceSessionResponse(
    @Json(name = "clientSecret") val clientSecret: String,
    @Json(name = "expiresAt") val expiresAt: String,
    @Json(name = "agent") val agent: String,
    @Json(name = "model") val model: String,
    @Json(name = "voice") val voice: String?,
    @Json(name = "session") val session: Map<String, Any>?,
    @Json(name = "turnDetection") val turnDetection: Map<String, Any>?,
    @Json(name = "iceServers") val iceServers: List<IceServerResponse>?,
    @Json(name = "transcriptsChannel") val transcriptsChannel: String,
    @Json(name = "controlChannel") val controlChannel: String,
)

data class IceServerResponse(
    @Json(name = "urls") val urls: List<String>?,
    @Json(name = "username") val username: String?,
    @Json(name = "credential") val credential: String?,
)
