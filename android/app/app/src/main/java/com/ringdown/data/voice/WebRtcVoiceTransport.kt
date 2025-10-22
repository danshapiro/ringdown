package com.ringdown.data.voice

import android.content.Context
import android.os.SystemClock
import android.util.Log
import com.ringdown.di.IoDispatcher
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.Locale
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
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
import org.json.JSONArray
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
import kotlin.math.abs

@Singleton
class WebRtcVoiceTransport @Inject constructor(
    @ApplicationContext private val context: Context,
    @IoDispatcher private val dispatcher: CoroutineDispatcher,
    private val diagnostics: VoiceDiagnosticsReporter
) : VoiceTransport {

    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(CONNECT_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)
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
    private var lastMicLevelEmitAt = 0L

    override suspend fun connect(parameters: VoiceTransport.ConnectParameters) {
        mutex.withLock {
            if (isConnected) {
                Log.i(TAG, "WebRTC transport already connected; ignoring duplicate connect call")
                return
            }

            withContext(dispatcher) {
                diagnostics.record(
                    VoiceDiagnosticType.CONNECT_ATTEMPT,
                    "Connecting to ${parameters.signalingUrl}",
                    metadata = mapOf("deviceId" to parameters.deviceId)
                )
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
                    diagnostics.record(
                        VoiceDiagnosticType.CONNECT_SUCCEEDED,
                        "Voice transport ready",
                        metadata = mapOf("deviceId" to parameters.deviceId)
                    )
                    Log.i(TAG, "WebRTC voice transport established for ${parameters.deviceId}")
                } catch (t: Throwable) {
                    Log.e(TAG, "Failed to establish WebRTC transport", t)
                    diagnostics.record(
                        VoiceDiagnosticType.CONNECT_FAILED,
                        "Failed to establish transport: ${t.message ?: t::class.java.simpleName}",
                        metadata = mapOf("deviceId" to parameters.deviceId)
                    )
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
        peerConnectionFactory?.let { return it }

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
                    .setAudioRecordErrorCallback(object : JavaAudioDeviceModule.AudioRecordErrorCallback {
                        override fun onWebRtcAudioRecordInitError(errorMessage: String) {
                            diagnostics.record(
                                VoiceDiagnosticType.AUDIO_DEVICE_ERROR,
                                "AudioRecord init error: $errorMessage"
                            )
                        }

                        override fun onWebRtcAudioRecordStartError(
                            errorCode: JavaAudioDeviceModule.AudioRecordStartErrorCode,
                            errorMessage: String
                        ) {
                            diagnostics.record(
                                VoiceDiagnosticType.AUDIO_DEVICE_ERROR,
                                "AudioRecord start error: $errorCode - $errorMessage"
                            )
                        }

                        override fun onWebRtcAudioRecordError(errorMessage: String) {
                            diagnostics.record(
                                VoiceDiagnosticType.AUDIO_DEVICE_ERROR,
                                "AudioRecord error: $errorMessage"
                            )
                        }
                    })
                    .setAudioTrackErrorCallback(object : JavaAudioDeviceModule.AudioTrackErrorCallback {
                        override fun onWebRtcAudioTrackInitError(errorMessage: String) {
                            diagnostics.record(
                                VoiceDiagnosticType.AUDIO_DEVICE_ERROR,
                                "AudioTrack init error: $errorMessage"
                            )
                        }

                        override fun onWebRtcAudioTrackStartError(
                            errorCode: JavaAudioDeviceModule.AudioTrackStartErrorCode,
                            errorMessage: String
                        ) {
                            diagnostics.record(
                                VoiceDiagnosticType.AUDIO_DEVICE_ERROR,
                                "AudioTrack start error: $errorCode - $errorMessage"
                            )
                        }

                        override fun onWebRtcAudioTrackError(errorMessage: String) {
                            diagnostics.record(
                                VoiceDiagnosticType.AUDIO_DEVICE_ERROR,
                                "AudioTrack error: $errorMessage"
                            )
                        }
                    })
                    .setAudioRecordStateCallback(object : JavaAudioDeviceModule.AudioRecordStateCallback {
                        override fun onWebRtcAudioRecordStart() {
                            diagnostics.record(
                                VoiceDiagnosticType.AUDIO_DEVICE_STATE,
                                "Microphone capture started"
                            )
                        }

                        override fun onWebRtcAudioRecordStop() {
                            diagnostics.record(
                                VoiceDiagnosticType.AUDIO_DEVICE_STATE,
                                "Microphone capture stopped"
                            )
                        }
                    })
                    .setSamplesReadyCallback(object : JavaAudioDeviceModule.SamplesReadyCallback {
                        override fun onWebRtcAudioRecordSamplesReady(samples: JavaAudioDeviceModule.AudioSamples) {
                            val data = samples.data
                            if (data.isEmpty()) {
                                return
                            }
                            val now = SystemClock.elapsedRealtime()
                            if (now - lastMicLevelEmitAt < MIC_LEVEL_LOG_INTERVAL_MS) {
                                return
                            }
                            lastMicLevelEmitAt = now
                            var index = 0
                            var sum = 0.0
                            var count = 0
                            while (index + 1 < data.size) {
                                val high = data[index + 1].toInt() and 0xFF
                                val low = data[index].toInt() and 0xFF
                                val sample = ((high shl 8) or low).toShort()
                                sum += abs(sample.toInt())
                                count += 1
                                index += 2
                            }
                            if (count == 0) {
                                return
                            }
                            val amplitude = sum / count
                            val amplitudeText = String.format(Locale.US, "%.1f", amplitude)
                            diagnostics.record(
                                VoiceDiagnosticType.MICROPHONE_LEVEL,
                                "Microphone amplitude ${amplitudeText}",
                                metadata = mapOf(
                                    "amplitude" to amplitude,
                                    "amplitude_fmt" to amplitudeText,
                                    "channels" to samples.channelCount,
                                    "sampleRate" to samples.sampleRate,
                                    "format" to samples.audioFormat
                                )
                            )
                        }
                    })
                    .setAudioTrackStateCallback(object : JavaAudioDeviceModule.AudioTrackStateCallback {
                        override fun onWebRtcAudioTrackStart() {
                            diagnostics.record(
                                VoiceDiagnosticType.AUDIO_DEVICE_STATE,
                                "Audio playback started"
                            )
                        }

                        override fun onWebRtcAudioTrackStop() {
                            diagnostics.record(
                                VoiceDiagnosticType.AUDIO_DEVICE_STATE,
                                "Audio playback stopped"
                            )
                        }
                    })
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
                    diagnostics.record(
                        VoiceDiagnosticType.PEER_STATE,
                        "Peer connection state -> $newState",
                        metadata = mapOf("state" to newState.name)
                    )
                    if (newState == PeerConnection.PeerConnectionState.FAILED && !connectionReady.isCompleted) {
                        connectionReady.completeExceptionally(
                            IllegalStateException("Peer connection failed")
                        )
                    }
                }

                override fun onIceConnectionChange(newState: PeerConnection.IceConnectionState) {
                    Log.d(TAG, "ICE connection state changed: $newState")
                    diagnostics.record(
                        VoiceDiagnosticType.ICE_STATE,
                        "ICE state -> $newState",
                        metadata = mapOf("state" to newState.name)
                    )
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

                override fun onDataChannel(dc: org.webrtc.DataChannel?) {
                    // Unused
                }

                override fun onRenegotiationNeeded() {
                    Log.d(TAG, "Renegotiation needed")
                }

                override fun onAddTrack(
                    receiver: org.webrtc.RtpReceiver?,
                    streams: Array<out org.webrtc.MediaStream>?
                ) {
                    val trackId = receiver?.id()
                    Log.d(TAG, "Remote track added: $trackId streams=${streams?.size ?: 0}")
                    diagnostics.record(
                        VoiceDiagnosticType.REMOTE_TRACK_ADDED,
                        "Remote track added",
                        metadata = mapOf("trackId" to trackId)
                    )
                    val remoteTrack = receiver?.track() as? AudioTrack
                    remoteTrack?.setVolume(1.0)
                }
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
                        diagnostics.record(
                            VoiceDiagnosticType.CONNECT_FAILED,
                            "Failed to publish SDP offer: ${t.message ?: t::class.java.simpleName}"
                        )
                        connectionReady.completeExceptionally(t)
                    }
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                signalingScope.launch {
                    try {
                        val json = JSONObject(text)
                        when (json.optString("type").lowercase(Locale.US)) {
                            "iceservers" -> {
                                val serversJson = json.optJSONArray("iceServers") ?: return@launch
                                val updatedServers = mutableListOf<PeerConnection.IceServer>()
                                for (index in 0 until serversJson.length()) {
                                    val entry = serversJson.opt(index)
                                    if (entry !is JSONObject) continue
                                    val urlsValue = entry.opt("urls")
                                    val urls = mutableListOf<String>()
                                    when (urlsValue) {
                                        is JSONArray -> {
                                            for (i in 0 until urlsValue.length()) {
                                                val url = urlsValue.optString(i)
                                                if (!url.isNullOrEmpty()) {
                                                    urls.add(url)
                                                }
                                            }
                                        }
                                        is String -> if (urlsValue.isNotBlank()) {
                                            urls.add(urlsValue)
                                        }
                                    }
                                    if (urls.isEmpty()) continue
                                    val builder = if (urls.size == 1) {
                                        PeerConnection.IceServer.builder(urls[0])
                                    } else {
                                        PeerConnection.IceServer.builder(urls)
                                    }
                                    val username = entry.optString("username")
                                    val credential = entry.optString("credential")
                                    if (!username.isNullOrEmpty() && !credential.isNullOrEmpty()) {
                                        builder.setUsername(username)
                                        builder.setPassword(credential)
                                    }
                                    updatedServers.add(builder.createIceServer())
                                }
                                if (updatedServers.isNotEmpty()) {
                                    val merged = mutableListOf(
                                        PeerConnection.IceServer.builder(DEFAULT_STUN_SERVER).createIceServer()
                                    )
                                    merged.addAll(updatedServers)
                                    val rtcConfig = PeerConnection.RTCConfiguration(merged).apply {
                                        sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
                                    }
                                    peer.setConfiguration(rtcConfig)
                                    diagnostics.record(
                                        VoiceDiagnosticType.ICE_SERVERS_RECEIVED,
                                        "Backend supplied ${merged.size} ICE entries",
                                        metadata = mapOf("count" to merged.size)
                                    )
                                    Log.d(TAG, "Updated ICE servers from backend: ${merged.size}")
                                }
                            }

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
                                diagnostics.record(
                                    VoiceDiagnosticType.REMOTE_CANDIDATE_APPLIED,
                                    "Applied remote ICE candidate",
                                    metadata = mapOf("mid" to (mid ?: ""), "index" to index)
                                )
                            }

                            else -> Log.d(TAG, "Ignoring signaling message: $text")
                        }
                    } catch (t: Throwable) {
                        Log.e(TAG, "Error handling signaling message", t)
                        diagnostics.record(
                            VoiceDiagnosticType.CONNECT_FAILED,
                            "Signaling message error: ${t.message ?: t::class.java.simpleName}"
                        )
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
                diagnostics.record(
                    VoiceDiagnosticType.CONNECT_FAILED,
                    "Signaling socket failure: ${t.message ?: t::class.java.simpleName}"
                )
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
        diagnostics.record(
            VoiceDiagnosticType.LOCAL_CANDIDATE_PUBLISHED,
            "Published local ICE candidate",
            metadata = mapOf(
                "mid" to candidate.sdpMid,
                "index" to candidate.sdpMLineIndex
            )
        )
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
        diagnostics.record(VoiceDiagnosticType.TEARDOWN, "Voice transport torn down")
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
        const val MIC_LEVEL_LOG_INTERVAL_MS = 750L
    }
}
