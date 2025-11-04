package com.ringdown.mobile.text

import com.ringdown.mobile.data.BackendEnvironment
import com.ringdown.mobile.domain.TextSessionBootstrap
import com.ringdown.mobile.util.MainDispatcherRule
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.take
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import org.junit.Assert.assertThrows
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
    fun `token traces capture assistant tokens`() = runTest {
        val backend = object : BackendEnvironment() {
            override fun baseUrl(): String = "https://example.com/"
        }

        val client = TextSessionClient(
            backendEnvironment = backend,
            baseClient = OkHttpClient(),
            dispatcher = dispatcherRule.dispatcher,
        )

        val traceDeferred = async {
            client.tokenTraces.first()
        }

        client.handleInboundMessage(
            """{"type":"assistant_token","token":"Hi","final":false,"messageType":"greeting"}"""
        )

        advanceUntilIdle()

        val trace = traceDeferred.await()
        assert(trace.token == "Hi")
        assert(!trace.final)
        assert(trace.messageType == "greeting")
    }

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

        val eventsDeferred = async {
            client.events.take(2).toList()
        }

        client.handleInboundMessage(
            """{"type":"ready","sessionId":"${bootstrap.sessionId}","agent":"${bootstrap.agent}","heartbeatIntervalSeconds":10,"heartbeatTimeoutSeconds":25}"""
        )
        client.handleInboundMessage(
            """{"type":"assistant_token","token":"Hello","final":true}"""
        )

        advanceUntilIdle()

        val events = eventsDeferred.await()
        val ready = events[0] as TextSessionEvent.Ready
        assert(ready.sessionId == bootstrap.sessionId)
        assert(ready.heartbeatIntervalSeconds == 10)
        val token = events[1] as TextSessionEvent.AssistantToken
        assert(token.token == "Hello")
        assert(token.final)
    }

    @Test
    fun `computeWebSocketEndpoint resolves relative paths`() {
        val endpoint = TextSessionClient.computeWebSocketEndpoint(
            baseUrl = "https://api.example.com/base/",
            websocketPath = "text/session",
        )
        assert(endpoint.httpUrl.toString() == "https://api.example.com/base/text/session")
        assert(endpoint.webSocketUrl == "wss://api.example.com/base/text/session")
    }

    @Test
    fun `computeWebSocketEndpoint handles absolute https path`() {
        val endpoint = TextSessionClient.computeWebSocketEndpoint(
            baseUrl = "https://ignored.example.com/",
            websocketPath = "https://tokens.example.net/v1/session",
        )
        assert(endpoint.httpUrl.toString() == "https://tokens.example.net/v1/session")
        assert(endpoint.webSocketUrl == "wss://tokens.example.net/v1/session")
    }

    @Test
    fun `computeWebSocketEndpoint handles wss url`() {
        val endpoint = TextSessionClient.computeWebSocketEndpoint(
            baseUrl = "https://ignored.example.com/",
            websocketPath = "wss://ws.example.net/bridge/socket",
        )
        assert(endpoint.httpUrl.toString() == "https://ws.example.net/bridge/socket")
        assert(endpoint.webSocketUrl == "wss://ws.example.net/bridge/socket")
    }

    @Test
    fun `computeWebSocketEndpoint rejects blank path`() {
        assertThrows(IllegalArgumentException::class.java) {
            TextSessionClient.computeWebSocketEndpoint(
                baseUrl = "https://api.example.com/",
                websocketPath = "   ",
            )
        }
    }
}
