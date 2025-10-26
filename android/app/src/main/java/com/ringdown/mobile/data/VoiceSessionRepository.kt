package com.ringdown.mobile.data

import android.util.Log
import com.ringdown.mobile.data.remote.VoiceApi
import com.ringdown.mobile.data.remote.VoiceSessionRequest
import com.ringdown.mobile.domain.IceServerConfig
import com.ringdown.mobile.domain.VoiceSessionBootstrap
import com.ringdown.mobile.di.IoDispatcher
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.withContext

private const val TAG = "VoiceSessionRepo"

@Singleton
class VoiceSessionRepository @Inject constructor(
    private val api: VoiceApi,
    @IoDispatcher private val dispatcher: CoroutineDispatcher,
) {

    suspend fun createSession(deviceId: String, agent: String?): VoiceSessionBootstrap =
        withContext(dispatcher) {
            Log.i(TAG, "Requesting realtime session for device=$deviceId agent=$agent")
            val response = api.createVoiceSession(
                VoiceSessionRequest(
                    deviceId = deviceId,
                    agent = agent,
                ),
            )

            val iceServers = response.iceServers.orEmpty().mapNotNull { payload ->
                val urls = payload.urls.orEmpty().filter { it.isNotBlank() }
                if (urls.isEmpty()) return@mapNotNull null
                IceServerConfig(
                    urls = urls,
                    username = payload.username,
                    credential = payload.credential,
                )
            }

            VoiceSessionBootstrap(
                clientSecret = response.clientSecret,
                model = response.model,
                voice = response.voice,
                transcriptsChannel = response.transcriptsChannel,
                controlChannel = response.controlChannel,
                iceServers = iceServers,
                turnDetection = response.turnDetection,
            )
        }
}
