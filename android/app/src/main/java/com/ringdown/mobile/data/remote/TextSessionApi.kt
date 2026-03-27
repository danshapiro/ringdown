package com.ringdown.mobile.data.remote

import com.squareup.moshi.Json
import retrofit2.http.Body
import retrofit2.http.POST

interface TextSessionApi {
    @POST("v1/mobile/text/session")
    suspend fun createTextSession(@Body payload: TextSessionRequest): TextSessionResponse
}

data class TextSessionRequest(
    @Json(name = "deviceId") val deviceId: String,
    @Json(name = "authToken") val authToken: String?,
    @Json(name = "agent") val agent: String?,
    @Json(name = "resumeToken") val resumeToken: String?,
)

data class TextSessionResponse(
    @Json(name = "sessionId") val sessionId: String,
    @Json(name = "sessionToken") val sessionToken: String,
    @Json(name = "resumeToken") val resumeToken: String?,
    @Json(name = "websocketPath") val websocketPath: String,
    @Json(name = "agent") val agent: String,
    @Json(name = "expiresAt") val expiresAt: String,
    @Json(name = "heartbeatIntervalSeconds") val heartbeatIntervalSeconds: Int?,
    @Json(name = "heartbeatTimeoutSeconds") val heartbeatTimeoutSeconds: Int?,
    @Json(name = "tlsPins") val tlsPins: List<String>?,
    @Json(name = "authToken") val authToken: String?,
    @Json(name = "history") val history: List<TextSessionHistoryMessage>? = null,
)

data class TextSessionHistoryMessage(
    @Json(name = "id") val id: String?,
    @Json(name = "role") val role: String?,
    @Json(name = "text") val text: String?,
    @Json(name = "timestampIso") val timestampIso: String?,
    @Json(name = "messageType") val messageType: String?,
    @Json(name = "toolPayload") val toolPayload: Map<String, Any?>?,
)
