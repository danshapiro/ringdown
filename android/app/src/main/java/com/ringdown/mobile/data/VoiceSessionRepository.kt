package com.ringdown.mobile.data

import android.util.Log
import com.ringdown.mobile.data.remote.VoiceApi
import com.ringdown.mobile.data.remote.VoiceSessionRequest
import com.ringdown.mobile.data.remote.ControlFetchRequest
import com.ringdown.mobile.domain.ControlMessage
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
    suspend fun fetchControlMessage(sessionId: String, controlKey: String): ControlMessage?
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

    override suspend fun fetchControlMessage(sessionId: String, controlKey: String): ControlMessage? =
        withContext(dispatcher) {
            val response = api.fetchControlMessage(
                controlKey = controlKey,
                payload = ControlFetchRequest(sessionId = sessionId),
            )
            val payload = response.message ?: return@withContext null
            val audioBase64 = payload.audioBase64?.takeIf { it.isNotBlank() } ?: return@withContext null
            val promptId = payload.promptId?.takeIf { it.isNotBlank() } ?: return@withContext null
            val messageId = payload.messageId?.takeIf { it.isNotBlank() } ?: "control-${System.currentTimeMillis()}"

            ControlMessage(
                messageId = messageId,
                promptId = promptId,
                audioBase64 = audioBase64,
                sampleRateHz = payload.sampleRateHz ?: 16_000,
                channels = payload.channels ?: 1,
                format = payload.format?.takeIf { it.isNotBlank() } ?: "pcm16",
                metadata = payload.metadata ?: emptyMap(),
                enqueuedAtIso = payload.enqueuedAt,
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
