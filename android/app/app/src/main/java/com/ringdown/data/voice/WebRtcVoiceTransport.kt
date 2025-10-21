package com.ringdown.data.voice

import android.content.Context
import android.net.Uri
import android.util.Log
import com.ringdown.di.IoDispatcher
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import org.webrtc.AudioSource
import org.webrtc.AudioTrack
import org.webrtc.IceCandidate
import org.webrtc.MediaConstraints
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.SdpObserver
import org.webrtc.SessionDescription
import org.webrtc.audio.JavaAudioDeviceModule
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher

@Singleton
class WebRtcVoiceTransport @Inject constructor(
    @ApplicationContext private val context: Context,
    @IoDispatcher private val dispatcher: CoroutineDispatcher
) : VoiceTransport {

    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(CONNECT_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS) // Long-lived WebSocket
        .writeTimeout(0, TimeUnit.SECONDS)
        .build()

    private val mutex = Mutex()
    private var peerConnectionFactory: PeerConnectionFactory? = null
    private var audioDeviceModule: JavaAudioDeviceModule? = null
    private var peerConnection: PeerConnection? = null
    private var audioSource: AudioSource? = null
    private var audioTrack: AudioTrack? = null
    private var signalingSocket: WebSocket? = null
    private var signalingScope = CoroutineScope(SupervisorJob() + dispatcher)
    private val pendingIceCandidates = mutableListOf<IceCandidate>()
    private var isConnected = false

    override suspend fun connect(parameters: VoiceTransport.ConnectParameters) {
        mutex.withLock {
            if (isConnected) {
                Log.i(TAG, "WebRTC transport already connected; ignoring duplicate connect call")
                return
            }

            withContext(dispatcher) {
                val factory = ensurePeerConnectionFactory()
                val connectionReady = CompletableDeferred<Unit>()
                val peer = createPeerConnection(factory, connectionReady)
                peerConnection = peer

                val audioConstraints = MediaConstraints()
                audioSource = factory.createAudioSource(audioConstraints)
                audioTrack = factory.createAudioTrack(AUDIO_TRACK_ID, audioSource).apply {
                    setEnabled(true)
                }
                peer.addTrack(audioTrack)

                val signalingUrl = buildSignalingUrl(parameters.signalingUrl, parameters.deviceId)
                val request = Request.Builder().url(signalingUrl).build()
                val listener = createWebSocketListener(peer, parameters.deviceId, connectionReady)

                signalingSocket = httpClient.newWebSocket(request, listener)

                try {
                    connectionReady.await()
                    isConnected = true
                    Log.i(TAG, "WebRTC voice transport established for ${parameters.deviceId}")
                } catch (t: Throwable) {
                    Log.e(TAG, "Failed to establish WebRTC transport", t)
                    teardownInternal()
                    throw t
                }
            }
        }
    }

    override suspend fun sendAudioFrame(frame: VoiceTransport.AudioFrame) {
        // Streaming is handled by WebRTC audio track; manual PCM injection not required.
    }

    override fun receiveAudioFrames(): Flow<VoiceTransport.AudioFrame> = emptyFlow()

    override suspend fun teardown() {
        mutex.withLock {
            withContext(dispatcher) {
                teardownInternal()
            }
        }
    }

    private suspend fun ensurePeerConnectionFactory(): PeerConnectionFactory {
        val existing = peerConnectionFactory
        if (existing != null) {
            return existing
        }

        return coroutineScope {
            val initJob = async(dispatcher) {
                PeerConnectionFactory.initialize(
                    PeerConnectionFactory.InitializationOptions.builder(context)
                        .setFieldTrials(DISABLE_VP8_FILTER)
                        .createInitializationOptions()
                )
                audioDeviceModule = JavaAudioDeviceModule.builder(context)
                    .setUseHardwareAcousticEchoCanceler(true)
                    .setUseHardwareNoiseSuppressor(true)
                    .createAudioDeviceModule()

                PeerConnectionFactory.builder()
                    .setAudioDeviceModule(audioDeviceModule)
                    .createPeerConnectionFactory()
            }

            val factory = initJob.await()
            peerConnectionFactory = factory
            factory
        }
    }

    private fun createPeerConnection(
        factory: PeerConnectionFactory,
        connectionReady: CompletableDeferred<Unit>
    ): PeerConnection {
        val iceServers = listOf(
            PeerConnection.IceServer.builder(DEFAULT_STUN_SERVER).createIceServer()
        )
        val rtcConfig = PeerConnection.RTCConfiguration(iceServers).apply {
            sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
        }

        return factory.createPeerConnection(
            rtcConfig,
            object : PeerConnection.Observer {
                override fun onIceCandidate(candidate: IceCandidate) {
                    val socket = signalingSocket
                    if (socket != null) {
                        sendCandidate(socket, candidate)
                    } else {
                        synchronized(pendingIceCandidates) {
                            pendingIceCandidates.add(candidate)
                        }
                    }
                }

                override fun onIceCandidatesRemoved(candidates: Array<out IceCandidate>) {
                    Log.d(TAG, "ICE candidates removed: ${candidates.size}")
                }

                override fun onConnectionChange(newState: PeerConnection.PeerConnectionState) {
                    Log.d(TAG, "Peer connection state changed: $newState")
                    if (newState == PeerConnection.PeerConnectionState.FAILED) {
                        connectionReady.completeExceptionally(
                            IllegalStateException("Peer connection failed")
                        )
                    }
                }

                override fun onIceConnectionChange(newState: PeerConnection.IceConnectionState) {
                    Log.d(TAG, "ICE connection state changed: $newState")
                }

                override fun onIceConnectionReceivingChange(receiving: Boolean) {
                    Log.d(TAG, "ICE connection receiving change: $receiving")
                }

                override fun onIceGatheringChange(newState: PeerConnection.IceGatheringState) {
                    Log.d(TAG, "ICE gathering state: $newState")
                }

                override fun onSignalingChange(newState: PeerConnection.SignalingState) {
                    Log.d(TAG, "Signaling state changed: $newState")
                }

                override fun onAddStream(stream: org.webrtc.MediaStream?) {
                    // Deprecated in Unified Plan but may be invoked; no-op.
                }

                override fun onRemoveStream(stream: org.webrtc.MediaStream?) {
                    // No-op
                }

                override fun onDataChannel(dc: org.webrtc.DataChannel?) {}

                override fun onRenegotiationNeeded() {
                    Log.d(TAG, "Renegotiation needed")
                }

                override fun onAddTrack(receiver: org.webrtc.RtpReceiver?, streams: Array<out org.webrtc.MediaStream>?) {}
            }
        ) ?: throw IllegalStateException("Unable to allocate PeerConnection")
    }

    private fun createWebSocketListener(
        peer: PeerConnection,
        deviceId: String,
        connectionReady: CompletableDeferred<Unit>
    ): WebSocketListener {
        return object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                signalingScope.launch {
                    try {
                        val offer = peer.createOfferSuspend(MediaConstraints())
                        peer.setLocalDescriptionSuspend(offer)
                        val payload = JSONObject()
                            .put("type", "offer")
                            .put("deviceId", deviceId)
                            .put("sdp", offer.description)
                        webSocket.send(payload.toString())

                        synchronized(pendingIceCandidates) {
                            pendingIceCandidates.forEach { candidate ->
                                sendCandidate(webSocket, candidate)
                            }
                            pendingIceCandidates.clear()
                        }
                    } catch (t: Throwable) {
                        Log.e(TAG, "Failed to publish SDP offer", t)
                        connectionReady.completeExceptionally(t)
                    }
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                signalingScope.launch {
                    try {
                        val json = JSONObject(text)
                        when (json.optString("type").lowercase(Locale.US)) {
                            "answer" -> {
                                val sdp = json.optString("sdp")
                                if (sdp.isNullOrEmpty()) {
                                    throw IllegalStateException("Answer missing SDP")
                                }
                                val answer = SessionDescription(SessionDescription.Type.ANSWER, sdp)
                                peer.setRemoteDescriptionSuspend(answer)
                                if (!connectionReady.isCompleted) {
                                    connectionReady.complete(Unit)
                                }
                            }

                            "candidate" -> {
                                val candidateJson = json.optJSONObject("candidate") ?: return@launch
                                val candidateValue = candidateJson.optString("candidate")
                                if (candidateValue.isNullOrEmpty()) return@launch
                                val mid = if (candidateJson.has("sdpMid")) {
                                    candidateJson.optString("sdpMid")
                                } else {
                                    null
                                }
                                val indexValue = candidateJson.opt("sdpMLineIndex")
                                val index = when (indexValue) {
                                    is Int -> indexValue
                                    is String -> indexValue.toIntOrNull()
                                    else -> null
                                } ?: 0
                                val candidate = IceCandidate(mid, index, candidateValue)
                                peer.addIceCandidate(candidate)
                            }

                            else -> Log.d(TAG, "Ignoring signaling message: $text")
                        }
                    } catch (t: Throwable) {
                        Log.e(TAG, "Error handling signaling message", t)
                        if (!connectionReady.isCompleted) {
                            connectionReady.completeExceptionally(t)
                        }
                    }
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "Voice signaling socket closing: $code / $reason")
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "Voice signaling socket closed: $code / $reason")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "Voice signaling socket failure", t)
                if (!connectionReady.isCompleted) {
                    connectionReady.completeExceptionally(t)
                }
            }
        }
    }

    private fun sendCandidate(webSocket: WebSocket, candidate: IceCandidate) {
        val payload = JSONObject()
            .put("type", "candidate")
            .put(
                "candidate",
                JSONObject()
                    .put("candidate", candidate.sdp)
                    .put("sdpMid", candidate.sdpMid)
                    .put("sdpMLineIndex", candidate.sdpMLineIndex)
            )
        webSocket.send(payload.toString())
    }

    private fun buildSignalingUrl(baseUrl: String, deviceId: String): String {
        val trimmed = baseUrl.trim()
        val httpUrl = trimmed.toHttpUrlOrNull()
            ?: throw IllegalArgumentException("Invalid signaling URL: $baseUrl")

        val rebuilt = httpUrl.newBuilder()
            .encodedPath("/")
            .addPathSegments("ws/mobile/voice")
            .addQueryParameter("device_id", deviceId)
            .build()

        return rebuilt.toString()
    }

    private suspend fun teardownInternal() {
        signalingSocket?.close(1000, "hangup")
        signalingSocket = null

        peerConnection?.close()
        peerConnection?.dispose()
        peerConnection = null

        audioTrack?.dispose()
        audioTrack = null

        audioSource?.dispose()
        audioSource = null

        synchronized(pendingIceCandidates) {
            pendingIceCandidates.clear()
        }

        signalingScope.cancel()
        signalingScope = CoroutineScope(SupervisorJob() + dispatcher)

        isConnected = false
    }

    private suspend fun PeerConnection.createOfferSuspend(constraints: MediaConstraints): SessionDescription =
        suspendCancellableCoroutine { continuation ->
            object : SdpObserver {
                override fun onCreateSuccess(desc: SessionDescription) {
                    continuation.resume(desc)
                }

                override fun onCreateFailure(error: String) {
                    continuation.resumeWithException(IllegalStateException("SDP offer failed: $error"))
                }

                override fun onSetSuccess() {}

                override fun onSetFailure(error: String) {
                    continuation.resumeWithException(IllegalStateException("SDP set failure: $error"))
                }
            }.also { observer ->
                createOffer(observer, constraints)
            }
        }

    private suspend fun PeerConnection.setLocalDescriptionSuspend(desc: SessionDescription) =
        suspendCancellableCoroutine<Unit> { continuation ->
            object : SdpObserver {
                override fun onSetSuccess() {
                    continuation.resume(Unit)
                }

                override fun onSetFailure(error: String) {
                    continuation.resumeWithException(IllegalStateException("Local SDP apply failed: $error"))
                }

                override fun onCreateSuccess(p0: SessionDescription?) {}

                override fun onCreateFailure(p0: String?) {}
            }.also { observer ->
                setLocalDescription(observer, desc)
            }
        }

    private suspend fun PeerConnection.setRemoteDescriptionSuspend(desc: SessionDescription) =
        suspendCancellableCoroutine<Unit> { continuation ->
            object : SdpObserver {
                override fun onSetSuccess() {
                    continuation.resume(Unit)
                }

                override fun onSetFailure(error: String) {
                    continuation.resumeWithException(IllegalStateException("Remote SDP apply failed: $error"))
                }

                override fun onCreateSuccess(p0: SessionDescription?) {}

                override fun onCreateFailure(p0: String?) {}
            }.also { observer ->
                setRemoteDescription(observer, desc)
            }
        }

    private companion object {
        const val TAG = "WebRtcVoiceTransport"
        const val AUDIO_TRACK_ID = "RD_AUDIO"
        const val DEFAULT_STUN_SERVER = "stun:stun.l.google.com:19302"
        const val CONNECT_TIMEOUT_SECONDS = 10L
        const val DISABLE_VP8_FILTER = "WebRTC-DisableVP8HSVC"
    }
}
