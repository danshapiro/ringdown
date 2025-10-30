package com.ringdown.mobile.data

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.remote.ControlFetchRequest
import com.ringdown.mobile.data.remote.ControlFetchResponse
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
                    sessionId = "session-123",
                    agent = payload.agent ?: "agent-a",
                    roomUrl = "https://daily.example.com/room",
                    accessToken = "token-xyz",
                    expiresAt = "2025-10-25T12:00:00Z",
                    pipelineSessionId = "pipeline-1",
                    greeting = "Hello",
                    metadata = mapOf(
                        "pipelineSessionId" to "pipeline-1",
                        "notes" to "demo",
                    ),
                )
            }

            override suspend fun fetchControlMessage(
                controlKey: String,
                payload: ControlFetchRequest,
            ): ControlFetchResponse {
                return ControlFetchResponse(message = null)
            }
        }

        val repository = VoiceSessionRepository(api, dispatcherRule.dispatcher)
        val session = repository.createSession("device-1", "agent-a")

        assertThat(session.sessionId).isEqualTo("session-123")
        assertThat(session.agent).isEqualTo("agent-a")
        assertThat(session.roomUrl).isEqualTo("https://daily.example.com/room")
        assertThat(session.accessToken).isEqualTo("token-xyz")
        assertThat(session.pipelineSessionId).isEqualTo("pipeline-1")
        assertThat(session.metadata).containsAtLeastEntriesIn(mapOf("notes" to "demo"))
    }
}
