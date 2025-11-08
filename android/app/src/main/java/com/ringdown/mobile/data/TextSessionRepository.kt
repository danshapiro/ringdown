package com.ringdown.mobile.data

import com.ringdown.mobile.data.remote.TextSessionApi
import com.ringdown.mobile.data.remote.TextSessionRequest
import com.ringdown.mobile.data.store.DeviceIdStore
import com.ringdown.mobile.di.IoDispatcher
import com.ringdown.mobile.domain.TextSessionBootstrap
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.withContext
import org.json.JSONObject
import retrofit2.HttpException

fun interface TextSessionStarter {
    suspend fun startTextSession(agent: String?): TextSessionBootstrap
}

@Singleton
class TextSessionRepository @Inject constructor(
    private val api: TextSessionApi,
    private val deviceIdStore: DeviceIdStore,
    @IoDispatcher private val dispatcher: CoroutineDispatcher,
) : TextSessionStarter {

    override suspend fun startTextSession(agent: String?): TextSessionBootstrap = withContext(dispatcher) {
        val deviceId = deviceIdStore.getOrCreateId()
        var attempt = 0
        while (true) {
            attempt += 1
            val response = try {
                api.createTextSession(
                    TextSessionRequest(
                        deviceId = deviceId,
                        authToken = deviceIdStore.currentAuthToken(),
                        agent = agent,
                        resumeToken = deviceIdStore.currentResumeToken(),
                    ),
                )
            } catch (error: Throwable) {
                if (error is HttpException) {
                    val errorCode = extractErrorCode(error)
                    if (error.code() == 401 && attempt == 1) {
                        deviceIdStore.updateAuthToken(null)
                        deviceIdStore.updateResumeToken(null)
                        continue
                    }
                    if (error.code() == 404 && attempt == 1 && errorCode == "resume_token_not_recognised") {
                        deviceIdStore.updateResumeToken(null)
                        continue
                    }
                    if (error.code() == 409 && attempt == 1 && errorCode == "session_already_active") {
                        deviceIdStore.updateResumeToken(null)
                        continue
                    }
                }
                throw error
            }

            if (!response.authToken.isNullOrBlank()) {
                deviceIdStore.updateAuthToken(response.authToken)
            }

            deviceIdStore.updateResumeToken(response.resumeToken)

            return@withContext TextSessionBootstrap(
                sessionId = response.sessionId,
                sessionToken = response.sessionToken,
                resumeToken = response.resumeToken,
                websocketPath = response.websocketPath,
                agent = response.agent,
                expiresAtIso = response.expiresAt,
                heartbeatIntervalSeconds = response.heartbeatIntervalSeconds ?: DEFAULT_HEARTBEAT_INTERVAL,
                heartbeatTimeoutSeconds = response.heartbeatTimeoutSeconds ?: DEFAULT_HEARTBEAT_TIMEOUT,
                tlsPins = response.tlsPins.orEmpty(),
            )
        }
        @Suppress("UNREACHABLE_CODE")
        throw IllegalStateException("Unable to obtain text session")
    }

    private fun extractErrorCode(error: HttpException): String? {
        val body = error.response()?.errorBody()?.string()
        if (body.isNullOrBlank()) {
            return null
        }

        val fromJson =
            try {
                val root = JSONObject(body)
                val detail = root.opt("detail")
                if (detail is JSONObject) {
                    detail.optString("code").takeIf { it.isNotBlank() }
                } else {
                    null
                }
            } catch (_: Exception) {
                null
            }

        return fromJson
            ?: body.takeIf { it.contains("resume_token_not_recognised") }?.let { "resume_token_not_recognised" }
            ?: body.takeIf { it.contains("session_already_active") }?.let { "session_already_active" }
    }

    companion object {
        private const val DEFAULT_HEARTBEAT_INTERVAL = 15
        private const val DEFAULT_HEARTBEAT_TIMEOUT = 45
    }
}
