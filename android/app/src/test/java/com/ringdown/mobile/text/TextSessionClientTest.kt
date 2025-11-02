package com.ringdown.mobile.text

import app.cash.turbine.test
import com.ringdown.mobile.data.BackendEnvironment
import com.ringdown.mobile.domain.TextSessionBootstrap
import com.ringdown.mobile.util.MainDispatcherRule
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TextSessionClientTest {

    @get:Rule
    val dispatcherRule = MainDispatcherRule()

    @Test
    fun `handleInboundMessage emits events`() = runTest {
        val backend = object : BackendEnvironment() {
            override fun baseUrl(): String = "https://example.com/"
        }

        val client = TextSessionClient(
            backendEnvironment = backend,
            baseClient = OkHttpClient(),
            dispatcher = dispatcherRule.dispatcher,
        )

        val bootstrap = TextSessionBootstrap(
            sessionId = "session-123",
            sessionToken = "token-abc",
            resumeToken = null,
            websocketPath = "/ws",
            agent = "demo-agent",
            expiresAtIso = "2025-10-25T12:00:00Z",
            heartbeatIntervalSeconds = 15,
            heartbeatTimeoutSeconds = 45,
            tlsPins = emptyList(),
        )

        client.events.test {
            client.handleInboundMessage(
                """{"type":"ready","sessionId":"${bootstrap.sessionId}","agent":"${bootstrap.agent}","heartbeatIntervalSeconds":10,"heartbeatTimeoutSeconds":25}"""
            )
            val ready = awaitItem() as TextSessionEvent.Ready
            assert(ready.sessionId == bootstrap.sessionId)
            assert(ready.heartbeatIntervalSeconds == 10)

            client.handleInboundMessage(
                """{"type":"assistant_token","token":"Hello","final":true}"""
            )
            val token = awaitItem() as TextSessionEvent.AssistantToken
            assert(token.token == "Hello")
            assert(token.final)

            cancelAndIgnoreRemainingEvents()
        }
    }
}
