package com.ringdown.mobile.data

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.remote.IceServerResponse
import com.ringdown.mobile.data.remote.VoiceApi
import com.ringdown.mobile.data.remote.VoiceSessionRequest
import com.ringdown.mobile.data.remote.VoiceSessionResponse
import com.ringdown.mobile.util.MainDispatcherRule
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class VoiceSessionRepositoryTest {

    @get:Rule
    val dispatcherRule = MainDispatcherRule()

    @Test
    fun `createSession maps response`() = runTest {
        val api = object : VoiceApi {
            override suspend fun createVoiceSession(payload: VoiceSessionRequest): VoiceSessionResponse {
                return VoiceSessionResponse(
                    clientSecret = "secret-value",
                    expiresAt = "2025-10-25T12:00:00Z",
                    agent = payload.agent ?: "agent-a",
                    model = "gpt-4o-realtime-preview",
                    voice = "alloy",
                    session = emptyMap(),
                    turnDetection = mapOf("type" to "server_vad"),
                    iceServers = listOf(
                        IceServerResponse(
                            urls = listOf("stun:stun.test:3478"),
                            username = null,
                            credential = null,
                        ),
                    ),
                    transcriptsChannel = "ringdown-transcripts",
                    controlChannel = "ringdown-control",
                )
            }
        }

        val repository = VoiceSessionRepository(api, dispatcherRule.dispatcher)
        val bootstrap = repository.createSession("device-1", "agent-a")

        assertThat(bootstrap.clientSecret).isEqualTo("secret-value")
        assertThat(bootstrap.model).isEqualTo("gpt-4o-realtime-preview")
        assertThat(bootstrap.iceServers).hasSize(1)
        assertThat(bootstrap.iceServers[0].urls).containsExactly("stun:stun.test:3478")
        assertThat(bootstrap.turnDetection).containsEntry("type", "server_vad")
    }
}
