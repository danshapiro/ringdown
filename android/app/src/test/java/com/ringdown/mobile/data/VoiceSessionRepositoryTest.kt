package com.ringdown.mobile.data

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.remote.ControlFetchRequest
import com.ringdown.mobile.data.remote.ControlFetchResponse
import com.ringdown.mobile.data.remote.VoiceApi
import com.ringdown.mobile.data.remote.VoiceSessionRequest
import com.ringdown.mobile.data.remote.VoiceSessionResponse
import com.ringdown.mobile.util.MainDispatcherRule
import java.time.Instant
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Before
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

    private val enableStubProperty = "ringdown.enable_registration_stub"
    private val disableStubProperty = "ringdown.disable_registration_stub"
    private var originalEnableStub: String? = null
    private var originalDisableStub: String? = null

    @Before
    fun captureOriginalProperties() {
        originalEnableStub = System.getProperty(enableStubProperty)
        originalDisableStub = System.getProperty(disableStubProperty)
    }

    @After
    fun restoreProperties() {
        if (originalEnableStub == null) {
            System.clearProperty(enableStubProperty)
        } else {
            System.setProperty(enableStubProperty, originalEnableStub)
        }

        if (originalDisableStub == null) {
            System.clearProperty(disableStubProperty)
        } else {
            System.setProperty(disableStubProperty, originalDisableStub)
        }
    }

    @Test
    fun createSessionMapsResponse() = runTest {
        System.clearProperty(enableStubProperty)
        System.setProperty(disableStubProperty, "true")

        val api = object : VoiceApi {
            override suspend fun createVoiceSession(payload: VoiceSessionRequest): VoiceSessionResponse {
                return VoiceSessionResponse(
                    sessionId = "session-123",
                    agent = payload.agent ?: "agent-a",
                    roomUrl = "https://daily.example.com/room",
                    accessToken = "token-xyz",
                    expiresAt = "2025-11-02T12:00:00Z",
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
            ): ControlFetchResponse = ControlFetchResponse(message = null)
        }

        val repository = VoiceSessionRepository(api, dispatcherRule.dispatcher)
        val session = repository.createSession("device-1", "agent-a")

        assertThat(session.sessionId).isEqualTo("session-123")
        assertThat(session.agent).isEqualTo("agent-a")
        assertThat(session.roomUrl).isEqualTo("https://daily.example.com/room")
        assertThat(session.accessToken).isEqualTo("token-xyz")
        assertThat(session.pipelineSessionId).isEqualTo("pipeline-1")
        assertThat(session.greeting).isEqualTo("Hello")
        assertThat(session.metadata).containsAtLeastEntriesIn(mapOf("notes" to "demo"))
    }

    @Test
    fun createSessionReturnsStubWhenEnabled() = runTest {
        System.setProperty(enableStubProperty, "true")
        System.clearProperty(disableStubProperty)

        val api = object : VoiceApi {
            override suspend fun createVoiceSession(payload: VoiceSessionRequest): VoiceSessionResponse {
                error("Stub path should bypass remote call")
            }

            override suspend fun fetchControlMessage(
                controlKey: String,
                payload: ControlFetchRequest,
            ): ControlFetchResponse = ControlFetchResponse(message = null)
        }

        val repository = VoiceSessionRepository(api, dispatcherRule.dispatcher)
        val session = repository.createSession("device-2", "agent-b")

        assertThat(session.sessionId).isEqualTo("stub-session-device-2")
        assertThat(session.agent).isEqualTo("agent-b")
        assertThat(session.roomUrl).contains("https://example.invalid/room")
        assertThat(session.accessToken).isEqualTo("stub-access-token")
        assertThat(session.pipelineSessionId).isEqualTo("stub-pipeline-device-2")
        assertThat(session.greeting).isEqualTo("Stub greeting ready.")
        assertThat(session.expiresAt.isAfter(Instant.now())).isTrue()
    }
}
