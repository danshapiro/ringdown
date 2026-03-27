package com.ringdown.mobile.data

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.ringdown.mobile.chat.ChatMessageRole
import com.ringdown.mobile.data.remote.TextSessionApi
import com.ringdown.mobile.data.remote.TextSessionHistoryMessage
import com.ringdown.mobile.data.remote.TextSessionRequest
import com.ringdown.mobile.data.remote.TextSessionResponse
import com.ringdown.mobile.data.store.DeviceIdStore
import java.io.File
import java.util.UUID
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

@OptIn(ExperimentalCoroutinesApi::class)
class TextSessionRepositoryTest {

    private val dispatcher = StandardTestDispatcher()

    @Test
    fun clearsStaleAuthTokenAndRetriesAfter401() = runTest(dispatcher) {
        val scope = TestScope(dispatcher)
        val store = DeviceIdStore(testDataStore(scope))
        val staleToken = "stale-token"
        store.updateAuthToken(staleToken)
        store.updateResumeToken("resume-old")

        val captures = mutableListOf<TextSessionRequest>()
        val api = object : TextSessionApi {
            private var attempts = 0
            override suspend fun createTextSession(payload: TextSessionRequest): TextSessionResponse {
                captures += payload
                attempts += 1
                return if (attempts == 1) {
                    throw unauthorized()
                } else {
                    TextSessionResponse(
                        sessionId = "session-abc",
                        sessionToken = "session-token",
                        resumeToken = "resume-new",
                        websocketPath = "/v1/mobile/text/session",
                        agent = "Agent Alpha",
                        expiresAt = "2025-11-02T00:00:00Z",
                        heartbeatIntervalSeconds = 15,
                        heartbeatTimeoutSeconds = 45,
                        tlsPins = emptyList(),
                        authToken = "fresh-token",
                    )
                }
            }
        }

        val repository = TextSessionRepository(api, store, dispatcher)
        val bootstrap = repository.startTextSession(null)
        val updatedAuth = store.currentAuthToken()
        val updatedResume = store.currentResumeToken()

        assertEquals("session-abc", bootstrap.sessionId)
        assertEquals(staleToken, captures.first().authToken)
        assertEquals("fresh-token", updatedAuth)
        assertEquals("resume-new", updatedResume)
        assertEquals(null, captures.last().authToken)
    }

    @Test
    fun clearsStaleResumeTokenAndRetriesAfter404() = runTest(dispatcher) {
        val scope = TestScope(dispatcher)
        val store = DeviceIdStore(testDataStore(scope))
        store.updateAuthToken("valid-token")
        store.updateResumeToken("resume-old")

        val captures = mutableListOf<TextSessionRequest>()
        val api = object : TextSessionApi {
            private var attempts = 0
            override suspend fun createTextSession(payload: TextSessionRequest): TextSessionResponse {
                captures += payload
                attempts += 1
                return if (attempts == 1) {
                    throw resumeNotFound()
                } else {
                    TextSessionResponse(
                        sessionId = "session-xyz",
                        sessionToken = "session-token",
                        resumeToken = "resume-fresh",
                        websocketPath = "/v1/mobile/text/session",
                        agent = "Agent Alpha",
                        expiresAt = "2025-11-02T00:00:00Z",
                        heartbeatIntervalSeconds = 20,
                        heartbeatTimeoutSeconds = 60,
                        tlsPins = emptyList(),
                        authToken = "valid-token",
                    )
                }
            }
        }

        val repository = TextSessionRepository(api, store, dispatcher)
        val bootstrap = repository.startTextSession(null)

        assertEquals("session-xyz", bootstrap.sessionId)
        assertEquals("resume-old", captures.first().resumeToken)
        assertNull(captures.last().resumeToken)
        assertEquals("resume-fresh", store.currentResumeToken())
    }

    @Test
    fun clearsResumeTokenWhenSessionAlreadyActive() = runTest(dispatcher) {
        val scope = TestScope(dispatcher)
        val store = DeviceIdStore(testDataStore(scope))
        store.updateAuthToken("valid-token")
        store.updateResumeToken("resume-old")

        val captures = mutableListOf<TextSessionRequest>()
        val api = object : TextSessionApi {
            private var attempts = 0
            override suspend fun createTextSession(payload: TextSessionRequest): TextSessionResponse {
                captures += payload
                attempts += 1
                return if (attempts == 1) {
                    throw sessionAlreadyActive()
                } else {
                    TextSessionResponse(
                        sessionId = "session-retry",
                        sessionToken = "session-token",
                        resumeToken = "resume-fresh",
                        websocketPath = "/v1/mobile/text/session",
                        agent = "Agent Alpha",
                        expiresAt = "2025-11-02T00:00:00Z",
                        heartbeatIntervalSeconds = 15,
                        heartbeatTimeoutSeconds = 45,
                        tlsPins = emptyList(),
                        authToken = "valid-token",
                    )
                }
            }
        }

        val repository = TextSessionRepository(api, store, dispatcher)
        val bootstrap = repository.startTextSession(null)

        assertEquals("session-retry", bootstrap.sessionId)
        assertEquals("resume-old", captures.first().resumeToken)
        assertNull(captures.last().resumeToken)
        assertEquals("resume-fresh", store.currentResumeToken())
    }

    @Test
    fun propagatesErrorWhenRetryAlsoFails() = runTest(dispatcher) {
        val scope = TestScope(dispatcher)
        val store = DeviceIdStore(testDataStore(scope))
        store.updateAuthToken("bad-token")

        val api = object : TextSessionApi {
            override suspend fun createTextSession(payload: TextSessionRequest): TextSessionResponse {
                throw unauthorized()
            }
        }

        val repository = TextSessionRepository(api, store, dispatcher)
        try {
            repository.startTextSession(null)
            throw AssertionError("Expected HttpException to be thrown")
        } catch (error: HttpException) {
            assertEquals(401, error.code())
        }
    }

    @Test
    fun populatesConversationHistoryFromResponse() = runTest(dispatcher) {
        val scope = TestScope(dispatcher)
        val store = DeviceIdStore(testDataStore(scope))

        val api = object : TextSessionApi {
            override suspend fun createTextSession(payload: TextSessionRequest): TextSessionResponse {
                return TextSessionResponse(
                    sessionId = "session-history",
                    sessionToken = "session-token",
                    resumeToken = "resume-token",
                    websocketPath = "/v1/mobile/text/session",
                    agent = "Agent Alpha",
                    expiresAt = "2025-11-02T00:00:00Z",
                    heartbeatIntervalSeconds = 15,
                    heartbeatTimeoutSeconds = 45,
                    tlsPins = emptyList(),
                    authToken = null,
                    history = listOf(
                        TextSessionHistoryMessage(
                            id = "m1",
                            role = "user",
                            text = "Hello",
                            timestampIso = "2025-11-08T00:00:00Z",
                            messageType = null,
                            toolPayload = null,
                        ),
                        TextSessionHistoryMessage(
                            id = "m2",
                            role = "tool",
                            text = "",
                            timestampIso = null,
                            messageType = "lookup",
                            toolPayload = mapOf("action" to "lookup"),
                        ),
                    ),
                )
            }
        }

        val repository = TextSessionRepository(api, store, dispatcher)
        val bootstrap = repository.startTextSession(null)

        assertEquals(2, bootstrap.history.size)
        assertEquals(ChatMessageRole.USER, bootstrap.history.first().role)
        assertEquals("Hello", bootstrap.history.first().text)
        assertEquals(ChatMessageRole.TOOL, bootstrap.history.last().role)
        assertEquals("lookup", bootstrap.history.last().messageType)
    }

    @Test
    fun clearsStoredResumeTokenWhenServerOmitsResumeToken() = runTest(dispatcher) {
        val scope = TestScope(dispatcher)
        val store = DeviceIdStore(testDataStore(scope))
        store.updateResumeToken("resume-old")

        val api = object : TextSessionApi {
            override suspend fun createTextSession(payload: TextSessionRequest): TextSessionResponse {
                return TextSessionResponse(
                    sessionId = "session-clean",
                    sessionToken = "session-token",
                    resumeToken = null,
                    websocketPath = "/v1/mobile/text/session",
                    agent = "Agent Alpha",
                    expiresAt = "2025-11-02T00:00:00Z",
                    heartbeatIntervalSeconds = 15,
                    heartbeatTimeoutSeconds = 45,
                    tlsPins = emptyList(),
                    authToken = null,
                )
            }
        }

        val repository = TextSessionRepository(api, store, dispatcher)
        repository.startTextSession(null)

        assertNull(store.currentResumeToken())
    }

    private fun unauthorized(): HttpException {
        val body = "{\"error\":\"unauthorized\"}".toResponseBody("application/json".toMediaType())
        val response = Response.error<TextSessionResponse>(401, body)
        return HttpException(response)
    }

    private fun resumeNotFound(): HttpException {
        val body =
            """
            {"detail":{"code":"resume_token_not_recognised","message":"Resume token not recognised or expired."}}
            """.trimIndent().toResponseBody("application/json".toMediaType())
        val response = Response.error<TextSessionResponse>(404, body)
        return HttpException(response)
    }

    private fun sessionAlreadyActive(): HttpException {
        val body =
            """
            {"detail":{"code":"session_already_active","message":"Session already active on another connection."}}
            """.trimIndent().toResponseBody("application/json".toMediaType())
        val response = Response.error<TextSessionResponse>(409, body)
        return HttpException(response)
    }

    private fun testDataStore(scope: TestScope): androidx.datastore.core.DataStore<androidx.datastore.preferences.core.Preferences> {
        val tempDir = File(System.getProperty("java.io.tmpdir"), "datastore-test-" + UUID.randomUUID())
        if (!tempDir.exists()) {
            tempDir.mkdirs()
        }
        return PreferenceDataStoreFactory.create(scope = scope) {
            File(tempDir, UUID.randomUUID().toString() + ".preferences_pb")
        }
    }
}
