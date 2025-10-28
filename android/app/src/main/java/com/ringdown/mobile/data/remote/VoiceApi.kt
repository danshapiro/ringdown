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
    @Json(name = "sessionId") val sessionId: String,
    @Json(name = "agent") val agent: String,
    @Json(name = "roomUrl") val roomUrl: String,
    @Json(name = "accessToken") val accessToken: String,
    @Json(name = "expiresAt") val expiresAt: String,
    @Json(name = "pipelineSessionId") val pipelineSessionId: String?,
    @Json(name = "greeting") val greeting: String?,
    @Json(name = "metadata") val metadata: Map<String, Any?>?,
)
