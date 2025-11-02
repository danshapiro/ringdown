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

interface TextSessionGateway {
    suspend fun startTextSession(agent: String? = null): TextSessionBootstrap
}

@Singleton
class TextSessionRepository @Inject constructor(
    private val api: TextSessionApi,
    private val deviceIdStore: DeviceIdStore,
    @IoDispatcher private val dispatcher: CoroutineDispatcher,
) : TextSessionGateway {

    override suspend fun startTextSession(agent: String?): TextSessionBootstrap = withContext(dispatcher) {
        val deviceId = deviceIdStore.getOrCreateId()
        val response = api.createTextSession(
            TextSessionRequest(
                deviceId = deviceId,
                authToken = deviceIdStore.currentAuthToken(),
                agent = agent,
                resumeToken = deviceIdStore.currentResumeToken(),
            ),
        )

        if (!response.authToken.isNullOrBlank()) {
            deviceIdStore.updateAuthToken(response.authToken)
        }

        deviceIdStore.updateResumeToken(response.resumeToken)

        TextSessionBootstrap(
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

    companion object {
        private const val DEFAULT_HEARTBEAT_INTERVAL = 15
        private const val DEFAULT_HEARTBEAT_TIMEOUT = 45
    }
}
