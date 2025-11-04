package com.ringdown.mobile.text

import android.util.Log
import com.ringdown.mobile.BuildConfig
import com.ringdown.mobile.data.BackendEnvironment
import com.ringdown.mobile.di.IoDispatcher
import com.ringdown.mobile.domain.TextSessionBootstrap
import java.time.Instant
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.CertificatePinner
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

@Singleton
open class TextSessionClient @Inject constructor(
    private val backendEnvironment: BackendEnvironment,
    private val baseClient: OkHttpClient,
    @IoDispatcher dispatcher: CoroutineDispatcher,
) {

    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val _events = MutableSharedFlow<TextSessionEvent>(
        extraBufferCapacity = 32,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    val events: SharedFlow<TextSessionEvent> = _events.asSharedFlow()
    private val _tokenTraces = MutableSharedFlow<AssistantTokenTrace>(
        extraBufferCapacity = 64,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    val tokenTraces: SharedFlow<AssistantTokenTrace> = _tokenTraces.asSharedFlow()

    private val _state = MutableStateFlow<TextSessionConnectionState>(TextSessionConnectionState.Idle)
    val state: StateFlow<TextSessionConnectionState> = _state.asStateFlow()

    private val lock = Mutex()
    private var activeWebSocket: WebSocket? = null
    private var heartbeatJob: Job? = null
    private var sessionInfo: SessionInfo? = null
    private var readyAcknowledged = false

    open suspend fun connect(bootstrap: TextSessionBootstrap) {
        lock.withLock {
            disconnectLocked()

            val endpoint = buildWebSocketEndpoint(bootstrap.websocketPath)
            val client = buildClientForPins(endpoint.httpUrl, bootstrap.tlsPins)

            val request = Request.Builder()
                .url(endpoint.webSocketUrl)
                .header(SESSION_TOKEN_HEADER, bootstrap.sessionToken)
                .header("User-Agent", userAgent())
                .build()

            _state.value = TextSessionConnectionState.Connecting

            val listener = SessionListener()
            val socket = client.newWebSocket(request, listener)

            sessionInfo = SessionInfo(
                bootstrap = bootstrap,
                client = client,
                url = endpoint.webSocketUrl,
            )
            activeWebSocket = socket
            readyAcknowledged = false
        }
    }

    open suspend fun disconnect() {
        lock.withLock {
            disconnectLocked()
        }
    }

    open suspend fun sendUserToken(token: String, final: Boolean, utteranceId: String?) {
        sendMessage(
            JSONObject()
                .put("type", "user_token")
                .put("token", token)
                .put("final", final)
                .also { obj ->
                    if (!utteranceId.isNullOrBlank()) {
                        obj.put("utteranceId", utteranceId)
                    }
                },
        )
    }

    open suspend fun sendUserMessage(text: String, utteranceId: String?) {
        sendMessage(
            JSONObject()
                .put("type", "user_message")
                .put("text", text)
                .also { obj ->
                    if (!utteranceId.isNullOrBlank()) {
                        obj.put("utteranceId", utteranceId)
                    }
                },
        )
    }

    open suspend fun sendCancel() {
        sendMessage(JSONObject().put("type", "cancel"))
    }

    private suspend fun sendMessage(payload: JSONObject) {
        val json = payload.toString()
        val socket = lock.withLock { activeWebSocket }
        if (socket == null) {
            Log.w(TAG, "Attempted to send message without active session: $json")
            return
        }
        val sent = socket.send(json)
        if (!sent) {
            Log.w(TAG, "WebSocket send returned false; enqueueing failure event")
            _events.emit(TextSessionEvent.SendFailed(json))
        }
    }

    private fun disconnectLocked() {
        heartbeatJob?.cancel()
        heartbeatJob = null

        activeWebSocket?.let { socket ->
            try {
                socket.close(1000, "client_shutdown")
            } catch (_: Exception) {
                // ignore
            }
        }
        activeWebSocket = null
        sessionInfo = null
        readyAcknowledged = false
        _state.value = TextSessionConnectionState.Idle
    }

    private fun buildClientForPins(url: HttpUrl, pins: List<String>): OkHttpClient {
        if (pins.isEmpty()) {
            return baseClient
        }
        val builder = CertificatePinner.Builder()
        pins.forEach { pin ->
            builder.add(url.host, pin)
        }
        return baseClient.newBuilder()
            .certificatePinner(builder.build())
            .pingInterval(30, TimeUnit.SECONDS)
            .build()
    }

    private fun buildWebSocketEndpoint(path: String): WebSocketEndpoint {
        return computeWebSocketEndpoint(
            baseUrl = backendEnvironment.baseUrl(),
            websocketPath = path,
        )
    }

    private fun startHeartbeat(intervalSeconds: Int) {
        heartbeatJob?.cancel()
        val intervalMillis = intervalSeconds
            .coerceAtLeast(5)
            .coerceAtMost(120)
            .times(1000L)

        heartbeatJob = scope.launch {
            delay(intervalMillis / 2)
            while (true) {
                try {
                    sendMessage(JSONObject().put("type", "heartbeat"))
                } catch (error: Exception) {
                    if (error is CancellationException) throw error
                    Log.w(TAG, "Failed to send heartbeat", error)
                }
                delay(intervalMillis)
            }
        }
    }

    internal fun handleInboundMessage(raw: String) {
        val message = try {
            JSONObject(raw)
        } catch (error: JSONException) {
            scope.launch {
                _events.emit(
                    TextSessionEvent.ProtocolError(
                        reason = "invalid_json",
                        detail = raw.take(MAX_ERROR_DETAIL),
                    ),
                )
            }
            return
        }

        val type = message.optString("type")
        when (type) {
            "ready" -> handleReady(message)
            "assistant_token" -> handleAssistantToken(message)
            "tool_event" -> handleToolEvent(message)
            "error" -> handleServerError(message)
            "heartbeat" -> handleHeartbeat()
            else -> scope.launch {
                _events.emit(
                    TextSessionEvent.ProtocolError(
                        reason = "unknown_type",
                        detail = type,
                    ),
                )
            }
        }
    }

    private fun handleReady(message: JSONObject) {
        val sessionId = message.optString("sessionId").takeIf { it.isNotBlank() }
        val agent = message.optString("agent").takeIf { it.isNotBlank() }
        val greeting = message.optString("greeting").takeIf { it.isNotBlank() }
        val intervalSeconds = message.optInt("heartbeatIntervalSeconds", 15).coerceAtLeast(5)
        val timeoutSeconds = message.optInt("heartbeatTimeoutSeconds", intervalSeconds + 5)

        logStructured(
            level = "INFO",
            event = "text_session.ready",
            fields = mapOf(
                "sessionId" to sessionId,
                "agent" to agent,
                "heartbeatIntervalSeconds" to intervalSeconds,
                "heartbeatTimeoutSeconds" to timeoutSeconds,
            ),
        )
        scope.launch {
            _events.emit(
                TextSessionEvent.Ready(
                    sessionId = sessionId,
                    agent = agent,
                    greeting = greeting,
                    heartbeatIntervalSeconds = intervalSeconds,
                    heartbeatTimeoutSeconds = timeoutSeconds,
                ),
            )
        }
        if (activeWebSocket != null) {
            startHeartbeat(intervalSeconds)
        }
        readyAcknowledged = true

        sessionInfo = sessionInfo?.copy(
            heartbeatIntervalSeconds = intervalSeconds,
            heartbeatTimeoutSeconds = timeoutSeconds,
        )

        _state.value = TextSessionConnectionState.Connected(
            sessionId = sessionId ?: sessionInfo?.bootstrap?.sessionId.orEmpty(),
            agent = agent ?: sessionInfo?.bootstrap?.agent.orEmpty(),
        )
    }

    private fun handleAssistantToken(message: JSONObject) {
        val token = message.optString("token", "")
        val finalFlag = message.optBoolean("final", false)
        val messageType = if (message.has("messageType")) {
            message.optString("messageType").takeIf { it.isNotBlank() }
        } else {
            null
        }
        val sessionId = sessionInfo?.bootstrap?.sessionId ?: message.optString("sessionId").takeIf { it.isNotBlank() }
        val receivedAt = Instant.now().toString()
        logStructured(
            level = "INFO",
            event = "text_session.assistant_token",
            fields = mapOf(
                "sessionId" to sessionId,
                "length" to token.length,
                "final" to finalFlag,
                "messageType" to messageType,
            ).let { base ->
                if (token.isEmpty()) base else base + ("token" to token)
            },
        )
        val event = TextSessionEvent.AssistantToken(
            token = token,
            final = finalFlag,
            messageType = messageType,
        )
        val trace = AssistantTokenTrace(
            token = token,
            final = finalFlag,
            messageType = messageType,
            sessionId = sessionId,
            receivedAtIso = receivedAt,
        )
        scope.launch {
            _events.emit(event)
            _tokenTraces.emit(trace)
        }
    }

    private fun handleToolEvent(message: JSONObject) {
        val eventType = if (message.has("event")) {
            message.optString("event").takeIf { it.isNotBlank() }
        } else {
            null
        }
        val payload = message.opt("payload")
        val payloadMap = when (payload) {
            is JSONObject -> payload.toMap()
            is JSONArray -> mapOf("items" to payload.toList())
            else -> emptyMap()
        }
        logStructured(
            level = "INFO",
            event = "text_session.tool_event",
            fields = mapOf(
                "event" to eventType,
                "payloadKeys" to payloadMap.keys.joinToString(","),
            ),
        )
        scope.launch {
            _events.emit(
                TextSessionEvent.ToolEvent(
                    event = eventType,
                    payload = payloadMap,
                ),
            )
        }
    }

    private fun handleServerError(message: JSONObject) {
        val code = message.optString("code").takeIf { it.isNotBlank() }
        val detail = message.optString("message").takeIf { it.isNotBlank() }
        logStructured(
            level = "ERROR",
            event = "text_session.server_error",
            fields = mapOf(
                "code" to code,
                "message" to detail,
            ),
        )
        scope.launch {
            _events.emit(
                TextSessionEvent.ServerError(
                    code = code,
                    message = detail,
                ),
            )
        }
    }

    private fun handleHeartbeat() {
        // Nothing else required for now; keep connection alive.
    }

    private fun onSocketClosed(code: Int, reason: String?) {
        Log.i(TAG, "WebSocket closed: code=$code reason=${reason.orEmpty()}")
        logStructured(
            level = "INFO",
            event = "text_session.socket_closed",
            fields = mapOf(
                "code" to code,
                "reason" to reason,
            ),
        )
        scope.launch {
            _events.emit(TextSessionEvent.ConnectionClosed(code, reason))
        }
        _state.value = TextSessionConnectionState.Closed(code, reason)
        scope.launch {
            lock.withLock {
                heartbeatJob?.cancel()
                heartbeatJob = null
                activeWebSocket = null
                readyAcknowledged = false
            }
        }
    }

    private fun onSocketFailure(error: Throwable) {
        if (error is CancellationException) return
        Log.e(TAG, "WebSocket failure", error)
        logStructured(
            level = "ERROR",
            event = "text_session.socket_failure",
            fields = mapOf("message" to (error.message ?: "unknown")),
        )
        scope.launch {
            _events.emit(TextSessionEvent.ConnectionFailure(error))
        }
        _state.value = TextSessionConnectionState.Failed(error.message ?: "WebSocket failure")
        scope.launch {
            lock.withLock {
                heartbeatJob?.cancel()
                heartbeatJob = null
                activeWebSocket = null
                readyAcknowledged = false
            }
        }
    }

    private inner class SessionListener : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            Log.i(TAG, "WebSocket opened (${response.code})")
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            handleInboundMessage(text)
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, reason)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            onSocketClosed(code, reason)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            onSocketFailure(t)
        }
    }

    private data class SessionInfo(
        val bootstrap: TextSessionBootstrap,
        val client: OkHttpClient,
        val url: String,
        val heartbeatIntervalSeconds: Int = bootstrap.heartbeatIntervalSeconds,
        val heartbeatTimeoutSeconds: Int = bootstrap.heartbeatTimeoutSeconds,
    )

    internal data class WebSocketEndpoint(
        val httpUrl: HttpUrl,
        val webSocketUrl: String,
    )

    private fun logStructured(
        level: String,
        event: String,
        fields: Map<String, Any?> = emptyMap(),
    ) {
        val payload = JSONObject()
        payload.put("severity", level)
        payload.put("event", event)
        fields.forEach { (key, value) ->
            if (value != null) {
                payload.put(key, value)
            }
        }
        val message = payload.toString()
        when (level.uppercase()) {
            "ERROR" -> Log.e(TAG, message)
            "WARNING", "WARN" -> Log.w(TAG, message)
            "DEBUG" -> Log.d(TAG, message)
            else -> Log.i(TAG, message)
        }
    }

    companion object {
        private const val TAG = "TextSessionClient"
        private const val SESSION_TOKEN_HEADER = "x-ringdown-session-token"
        private const val MAX_ERROR_DETAIL = 256

        internal fun computeWebSocketEndpoint(
            baseUrl: String,
            websocketPath: String,
        ): WebSocketEndpoint {
            val trimmedPath = websocketPath.trim()
            require(trimmedPath.isNotEmpty()) { "websocketPath must not be blank" }

            val (httpUrl, wsSchemeOverride) = when {
                trimmedPath.startsWith("ws://", ignoreCase = true) ->
                    requireHttpUrl("http://${trimmedPath.substringAfter("://")}") to "ws"
                trimmedPath.startsWith("wss://", ignoreCase = true) ->
                    requireHttpUrl("https://${trimmedPath.substringAfter("://")}") to "wss"
                trimmedPath.startsWith("http://", ignoreCase = true) ->
                    requireHttpUrl(trimmedPath) to "ws"
                trimmedPath.startsWith("https://", ignoreCase = true) ->
                    requireHttpUrl(trimmedPath) to "wss"
                else -> {
                    val trimmedBase = baseUrl.trim()
                    require(trimmedBase.isNotEmpty()) {
                        "baseUrl must not be blank when websocketPath is relative"
                    }
                    val baseHttpUrl = requireHttpUrl(trimmedBase)
                    val resolved = baseHttpUrl.resolve(trimmedPath)
                        ?: baseHttpUrl.resolve("/${trimmedPath.trimStart('/')}")
                        ?: throw IllegalArgumentException(
                            "Unable to resolve websocket path: $websocketPath",
                        )
                    resolved to null
                }
            }

            val wsScheme = wsSchemeOverride ?: if (httpUrl.isHttps) "wss" else "ws"
            val webSocketUrl = buildWebSocketUrl(httpUrl, wsScheme)

            return WebSocketEndpoint(httpUrl = httpUrl, webSocketUrl = webSocketUrl)
        }

        private fun requireHttpUrl(value: String): HttpUrl {
            val trimmed = value.trim()
            if (trimmed.isEmpty()) {
                throw IllegalArgumentException("Invalid HTTP URL: $value")
            }
            return trimmed.toHttpUrlOrNull()
                ?: throw IllegalArgumentException("Invalid HTTP URL: $value")
        }

        private fun buildWebSocketUrl(httpUrl: HttpUrl, scheme: String): String {
            val builder = StringBuilder()
            builder.append(scheme)
            builder.append("://")
            builder.append(httpUrl.host)

            val normalisedScheme = scheme.lowercase()
            val defaultPort = when (normalisedScheme) {
                "wss" -> 443
                "ws" -> 80
                else -> -1
            }
            val port = httpUrl.port
            if (port > 0 && port != defaultPort) {
                builder.append(":").append(port)
            }

            builder.append(httpUrl.encodedPath)

            httpUrl.encodedQuery?.takeIf { it.isNotEmpty() }?.let { query ->
                builder.append('?').append(query)
            }

            httpUrl.encodedFragment?.takeIf { it.isNotEmpty() }?.let { fragment ->
                builder.append('#').append(fragment)
            }

            return builder.toString()
        }

        private fun JSONObject.toMap(): Map<String, Any?> {
            val result = mutableMapOf<String, Any?>()
            val iter = keys()
            while (iter.hasNext()) {
                val key = iter.next()
                if (key.isNullOrBlank()) continue
                result[key] = when (val value = opt(key)) {
                    is JSONObject -> value.toMap()
                    is JSONArray -> value.toList()
                    JSONObject.NULL -> null
                    else -> value
                }
            }
            return result
        }

        private fun JSONArray.toList(): List<Any?> {
            if (length() == 0) return emptyList()
            val values = ArrayList<Any?>(length())
            for (index in 0 until length()) {
                val value = opt(index)
                values += when (value) {
                    is JSONObject -> value.toMap()
                    is JSONArray -> value.toList()
                    JSONObject.NULL -> null
                    else -> value
                }
            }
            return values
        }

        private fun userAgent(): String {
            return "RingdownAndroid/${BuildConfig.VERSION_NAME}"
        }
    }
}

sealed class TextSessionConnectionState {
    object Idle : TextSessionConnectionState()
    object Connecting : TextSessionConnectionState()
    data class Connected(val sessionId: String, val agent: String) : TextSessionConnectionState()
    data class Closed(val code: Int, val reason: String?) : TextSessionConnectionState()
    data class Failed(val reason: String) : TextSessionConnectionState()
}

sealed class TextSessionEvent {
    data class Ready(
        val sessionId: String?,
        val agent: String?,
        val greeting: String?,
        val heartbeatIntervalSeconds: Int,
        val heartbeatTimeoutSeconds: Int,
    ) : TextSessionEvent()

    data class AssistantToken(
        val token: String,
        val final: Boolean,
        val messageType: String?,
    ) : TextSessionEvent()

    data class ToolEvent(
        val event: String?,
        val payload: Map<String, Any?>,
    ) : TextSessionEvent()

    data class ServerError(val code: String?, val message: String?) : TextSessionEvent()
    data class ConnectionClosed(val code: Int, val reason: String?) : TextSessionEvent()
    data class ConnectionFailure(val error: Throwable) : TextSessionEvent()
    data class ProtocolError(val reason: String, val detail: String?) : TextSessionEvent()
    data class SendFailed(val payload: String) : TextSessionEvent()
}
