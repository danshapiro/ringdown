package com.ringdown.mobile.data

import android.util.Log
import com.ringdown.mobile.data.remote.VoiceApi
import com.ringdown.mobile.data.remote.VoiceSessionRequest
import com.ringdown.mobile.domain.ManagedVoiceSession
import com.ringdown.mobile.di.IoDispatcher
import java.time.Instant
import java.time.format.DateTimeParseException
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.withContext

interface VoiceSessionDataSource {
    suspend fun createSession(deviceId: String, agent: String?): ManagedVoiceSession
}

private const val TAG = "VoiceSessionRepo"

@Singleton
class VoiceSessionRepository @Inject constructor(
    private val api: VoiceApi,
    @IoDispatcher private val dispatcher: CoroutineDispatcher,
) : VoiceSessionDataSource {

    override suspend fun createSession(deviceId: String, agent: String?): ManagedVoiceSession =
        withContext(dispatcher) {
            Log.i(TAG, "Requesting managed session for device=$deviceId agent=$agent")
            val response = api.createVoiceSession(
                VoiceSessionRequest(
                    deviceId = deviceId,
                    agent = agent,
                ),
            )

            ManagedVoiceSession(
                sessionId = response.sessionId.requireNonBlank("sessionId"),
                agent = response.agent.requireNonBlank("agent"),
                roomUrl = response.roomUrl.requireNonBlank("roomUrl"),
                accessToken = response.accessToken.requireNonBlank("accessToken"),
                expiresAt = parseExpiry(response.expiresAt),
                pipelineSessionId = response.pipelineSessionId?.takeIf { it.isNotBlank() },
                metadata = normaliseMetadata(response.metadata),
                greeting = response.greeting?.takeIf { it.isNotBlank() },
            )
        }

    private fun parseExpiry(raw: String): Instant {
        val trimmed = raw.trim()
        if (trimmed.isEmpty()) {
            throw IllegalStateException("expiresAt missing from voice session response")
        }
        return try {
            Instant.parse(trimmed)
        } catch (error: DateTimeParseException) {
            throw IllegalStateException("expiresAt is not ISO-8601: $trimmed", error)
        }
    }

    private fun normaliseMetadata(source: Map<String, Any?>?): Map<String, Any?> {
        if (source.isNullOrEmpty()) {
            return emptyMap()
        }
        val result = mutableMapOf<String, Any?>()
        for ((key, value) in source) {
            if (key.isBlank()) {
                continue
            }
            result[key] = value
        }
        return result.toMap()
    }
}

private fun String?.requireNonBlank(field: String): String {
    val value = this?.trim().orEmpty()
    if (value.isEmpty()) {
        throw IllegalStateException("$field missing from voice session response")
    }
    return value
}
