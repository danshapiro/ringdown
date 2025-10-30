package com.ringdown.mobile.data.remote

import com.squareup.moshi.Json
import retrofit2.http.Body
import retrofit2.http.Header
import retrofit2.http.POST

interface VoiceApi {
    @POST("v1/mobile/voice/session")
    suspend fun createVoiceSession(@Body payload: VoiceSessionRequest): VoiceSessionResponse

    @POST("v1/mobile/managed-av/control/next")
    suspend fun fetchControlMessage(
        @Header("X-Ringdown-Control-Key") controlKey: String,
        @Body payload: ControlFetchRequest,
    ): ControlFetchResponse
}

data class VoiceSessionRequest(
    @Json(name = "deviceId") val deviceId: String,
    @Json(name = "agent") val agent: String?,
)

data class VoiceSessionResponse(
    @Json(name = "sessionId") val sessionId: String,
    @Json(name = "agent") val agent: String,
    @Json(name = "roomUrl") val roomUrl: String,
    @Json(name = "accessToken") val accessToken: String,
    @Json(name = "expiresAt") val expiresAt: String,
    @Json(name = "pipelineSessionId") val pipelineSessionId: String?,
    @Json(name = "greeting") val greeting: String?,
    @Json(name = "metadata") val metadata: Map<String, Any?>?,
)

data class ControlFetchRequest(
    @Json(name = "sessionId") val sessionId: String,
)

data class ControlMessagePayload(
    @Json(name = "messageId") val messageId: String?,
    @Json(name = "promptId") val promptId: String?,
    @Json(name = "audioBase64") val audioBase64: String?,
    @Json(name = "sampleRateHz") val sampleRateHz: Int?,
    @Json(name = "channels") val channels: Int?,
    @Json(name = "format") val format: String?,
    @Json(name = "metadata") val metadata: Map<String, Any?>?,
    @Json(name = "enqueuedAt") val enqueuedAt: String?,
)

data class ControlFetchResponse(
    @Json(name = "message") val message: ControlMessagePayload?,
)
