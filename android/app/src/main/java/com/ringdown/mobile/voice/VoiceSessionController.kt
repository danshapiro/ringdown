package com.ringdown.mobile.voice

import android.content.Context
import android.util.Log
import com.ringdown.mobile.data.BackendEnvironment
import com.ringdown.mobile.data.VoiceSessionRepository
import com.ringdown.mobile.di.IoDispatcher
import com.ringdown.mobile.domain.IceServerConfig
import com.ringdown.mobile.domain.VoiceSessionBootstrap
import com.squareup.moshi.Json
import com.squareup.moshi.Moshi
import dagger.hilt.android.qualifiers.ApplicationContext
import java.nio.charset.StandardCharsets
import java.util.concurrent.atomic.AtomicBoolean
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okio.ByteString
import org.json.JSONException
import org.json.JSONObject
import org.webrtc.AudioSource
import org.webrtc.AudioTrack
import org.webrtc.DataChannel
import org.webrtc.DefaultVideoDecoderFactory
import org.webrtc.DefaultVideoEncoderFactory
import org.webrtc.EglBase
import org.webrtc.IceCandidate
import org.webrtc.MediaConstraints
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.RtpReceiver
import org.webrtc.RtpTransceiver
import org.webrtc.SessionDescription
import org.webrtc.audio.AudioDeviceModule
import org.webrtc.audio.JavaAudioDeviceModule
import org.webrtc.audio.JavaAudioDeviceModule.AudioRecordErrorCallback
import org.webrtc.audio.JavaAudioDeviceModule.AudioTrackErrorCallback
import org.webrtc.audio.JavaAudioDeviceModule.AudioRecordStateCallback
import org.webrtc.audio.JavaAudioDeviceModule.AudioTrackStateCallback

private const val TAG = "VoiceSession"

sealed class VoiceConnectionState {
    object Idle : VoiceConnectionState()
    object Connecting : VoiceConnectionState()
    data class Connected(val transcripts: List<TranscriptMessage>) : VoiceConnectionState()
    data class Failed(val reason: String) : VoiceConnectionState()
}

data class TranscriptMessage(
    val speaker: String,
    val text: String,
    val timestampIso: String?,
)

@Singleton
class VoiceSessionController @Inject constructor(
    @ApplicationContext private val context: Context,
    private val repository: VoiceSessionRepository,
    private val backendEnvironment: BackendEnvironment,
    private val okHttpClient: OkHttpClient,
    private val moshi: Moshi,
    @IoDispatcher dispatcher: CoroutineDispatcher,
) : VoiceSessionGateway {

    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val _state = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Idle)
    override val state: StateFlow<VoiceConnectionState> = _state.asStateFlow()

    private var peerConnectionFactory: PeerConnectionFactory? = null
    private var audioDeviceModule: AudioDeviceModule? = null
    private var audioSource: AudioSource? = null
    private var localAudioTrack: AudioTrack? = null
    private var peerConnection: PeerConnection? = null
    private var transcriptsChannel: DataChannel? = null
    private var controlChannel: DataChannel? = null
    private var webSocket: WebSocket? = null
    private var sessionJobActive = AtomicBoolean(false)
    private var eglBase: EglBase? = null

    private var iceGatheringDeferred: CompletableDeferred<Unit>? = null

    private val pendingCandidates: MutableList<IceCandidate> = mutableListOf()
    private var pendingOffer: String? = null

    private val transcripts: MutableList<TranscriptMessage> = mutableListOf()
    private val transcriptAdapter by lazy {
        moshi.adapter(TranscriptPayload::class.java)
    }

    override fun start(deviceId: String, agent: String?) {
        if (!sessionJobActive.compareAndSet(false, true)) {
            Log.w(TAG, "Voice session already running; ignoring start request")
            return
        }

        scope.launch {
            try {
                _state.value = VoiceConnectionState.Connecting
                transcripts.clear()

                val bootstrap = repository.createSession(deviceId, agent)
                setupPeerConnection(bootstrap)
                val offerSdp = createOffer()
                pendingOffer = offerSdp
                openSignalingSocket(deviceId)
                sendOfferIfReady()
            } catch (error: Exception) {
                if (error is CancellationException) {
                    throw error
                }
                Log.e(TAG, "Unable to start voice session", error)
                _state.value = VoiceConnectionState.Failed(error.message ?: "Call failed")
                cleanup()
            }
        }
    }

    override fun stop() {
        if (!sessionJobActive.compareAndSet(true, false)) {
            return
        }
        scope.launch {
            cleanup()
        }
    }

    private suspend fun setupPeerConnection(bootstrap: VoiceSessionBootstrap) {
        ensureFactory()

        val factory = peerConnectionFactory ?: throw IllegalStateException("PeerConnectionFactory not initialised")

        val rtcConfig = PeerConnection.RTCConfiguration(buildIceServers(bootstrap.iceServers)).apply {
            sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
        }

        val observer = object : PeerConnection.Observer {
            override fun onSignalingChange(newState: PeerConnection.SignalingState) {
                Log.d(TAG, "Signaling state: $newState")
            }

            override fun onIceConnectionChange(newState: PeerConnection.IceConnectionState) {
                Log.d(TAG, "ICE connection state: $newState")
                if (newState == PeerConnection.IceConnectionState.FAILED ||
                    newState == PeerConnection.IceConnectionState.DISCONNECTED ||
                    newState == PeerConnection.IceConnectionState.CLOSED
                ) {
                    _state.value = VoiceConnectionState.Failed("Connection lost")
                    stop()
                }
            }

            override fun onStandardizedIceConnectionChange(newState: PeerConnection.IceConnectionState) {
                Log.d(TAG, "Standardised ICE state: $newState")
            }

            override fun onIceConnectionReceivingChange(receiving: Boolean) {
                Log.d(TAG, "ICE receiving: $receiving")
            }

            override fun onIceGatheringChange(newState: PeerConnection.IceGatheringState) {
                Log.d(TAG, "ICE gathering state: $newState")
                if (newState == PeerConnection.IceGatheringState.COMPLETE) {
                    iceGatheringDeferred?.complete(Unit)
                }
            }

            override fun onIceCandidate(candidate: IceCandidate) {
                sendCandidate(candidate)
            }

            override fun onIceCandidatesRemoved(candidates: Array<IceCandidate>) {
                Log.d(TAG, "ICE candidates removed: ${candidates.size}")
            }

            override fun onAddStream(stream: org.webrtc.MediaStream?) {
                // Deprecated API; no-op.
            }

            override fun onRemoveStream(stream: org.webrtc.MediaStream?) {
                // Deprecated API; no-op.
            }

            override fun onDataChannel(channel: DataChannel) {
                Log.d(TAG, "Remote data channel: ${channel.label()}")
                if (channel.label() == bootstrap.transcriptsChannel) {
                    configureTranscriptsChannel(channel)
                } else if (channel.label() == bootstrap.controlChannel) {
                    configureControlChannel(channel)
                } else {
                    channel.close()
                    channel.dispose()
                }
            }

            override fun onRenegotiationNeeded() {
                Log.d(TAG, "Renegotiation needed")
            }

            override fun onAddTrack(receiver: RtpReceiver, streams: Array<org.webrtc.MediaStream>) {
                Log.d(TAG, "Track added: ${receiver.id()}")
                val track = receiver.track()
                if (track is AudioTrack) {
                    track.setEnabled(true)
                    track.setVolume(1.0)
                    _state.value = VoiceConnectionState.Connected(transcripts.toList())
                }
            }

            override fun onTrack(transceiver: RtpTransceiver) {
                val track = transceiver.receiver.track()
                Log.d(TAG, "Transceiver track: ${track?.kind()}")
                if (track is AudioTrack) {
                    track.setEnabled(true)
                    track.setVolume(1.0)
                    _state.value = VoiceConnectionState.Connected(transcripts.toList())
                }
            }
        }

        val peer = factory.createPeerConnection(rtcConfig, observer)
            ?: throw IllegalStateException("Unable to create peer connection")
        peerConnection = peer

        val transcriptsInit = DataChannel.Init().apply {
            ordered = true
            negotiated = true
            id = 0
        }
        configureTranscriptsChannel(peer.createDataChannel(bootstrap.transcriptsChannel, transcriptsInit))

        val controlInit = DataChannel.Init().apply {
            ordered = false
            negotiated = true
            id = 1
        }
        configureControlChannel(peer.createDataChannel(bootstrap.controlChannel, controlInit))

        val audioConstraints = MediaConstraints()
        audioSource = factory.createAudioSource(audioConstraints)
        val localTrack = factory.createAudioTrack("ringdown-local-audio", audioSource)
        localTrack.setEnabled(true)
        localAudioTrack = localTrack
        peer.addTrack(localTrack)
    }

    private fun configureTranscriptsChannel(channel: DataChannel) {
        transcriptsChannel = channel
        channel.registerObserver(object : DataChannel.Observer {
            override fun onBufferedAmountChange(previousAmount: Long) {
                // no-op
            }

            override fun onStateChange() {
                Log.d(TAG, "Transcripts channel state=${channel.state()}")
            }

            override fun onMessage(buffer: DataChannel.Buffer) {
                if (buffer.binary) {
                    return
                }
                val data = buffer.data
                val bytes = ByteArray(data.remaining())
                data.get(bytes)
                handleTranscriptPayload(String(bytes, StandardCharsets.UTF_8))
            }
        })
    }

    private fun configureControlChannel(channel: DataChannel) {
        controlChannel = channel
        channel.registerObserver(object : DataChannel.Observer {
            override fun onBufferedAmountChange(previousAmount: Long) {
                // no-op
            }

            override fun onStateChange() {
                Log.d(TAG, "Control channel state=${channel.state()}")
            }

            override fun onMessage(buffer: DataChannel.Buffer) {
                if (buffer.binary) {
                    return
                }
                val data = buffer.data
                val bytes = ByteArray(data.remaining())
                data.get(bytes)
                Log.d(TAG, "Control message: ${String(bytes, StandardCharsets.UTF_8)}")
            }
        })
    }

    private suspend fun createOffer(): String {
        val peer = peerConnection ?: throw IllegalStateException("Peer connection not available")
        val constraints = MediaConstraints().apply {
            mandatory.add(MediaConstraints.KeyValuePair("OfferToReceiveAudio", "true"))
        }

        val offer = peer.createOfferAwait(constraints)

        val gatheringDeferred = CompletableDeferred<Unit>()
        iceGatheringDeferred = gatheringDeferred
        peer.setLocalDescriptionAwait(offer)

        if (peer.iceGatheringState() == PeerConnection.IceGatheringState.COMPLETE) {
            gatheringDeferred.complete(Unit)
        }
        try {
            gatheringDeferred.await()
        } catch (error: CancellationException) {
            throw error
        } finally {
            iceGatheringDeferred = null
        }

        val local = peer.localDescription ?: throw IllegalStateException("Local description missing")
        return local.description
    }

    private fun sendOfferIfReady() {
        val ws = webSocket ?: return
        val offer = pendingOffer ?: return
        val payload = JSONObject()
        try {
            payload.put("type", "offer")
            payload.put("sdp", offer)
        } catch (error: JSONException) {
            Log.e(TAG, "Failed to encode offer", error)
            return
        }
        Log.d(TAG, "Sending offer")
        ws.send(payload.toString())
        pendingOffer = null
    }

    private fun sendCandidate(candidate: IceCandidate) {
        val ws = webSocket
        if (ws == null) {
            pendingCandidates += candidate
            return
        }

        val candidateJson = JSONObject()
        val wrapper = JSONObject()
        try {
            candidateJson.put("candidate", candidate.sdp)
            candidateJson.put("sdpMid", candidate.sdpMid)
            candidateJson.put("sdpMLineIndex", candidate.sdpMLineIndex)
            wrapper.put("type", "candidate")
            wrapper.put("candidate", candidateJson)
        } catch (error: JSONException) {
            Log.e(TAG, "Failed to encode candidate", error)
            return
        }

        Log.d(TAG, "Sending ICE candidate")
        ws.send(wrapper.toString())
    }

    private fun flushPendingCandidates() {
        val ws = webSocket ?: return
        val iterator = pendingCandidates.iterator()
        while (iterator.hasNext()) {
            sendCandidate(iterator.next())
            iterator.remove()
        }
    }

    private fun handleTranscriptPayload(json: String) {
        val payload = try {
            transcriptAdapter.fromJson(json)
        } catch (error: Exception) {
            Log.w(TAG, "Failed to decode transcript payload", error)
            null
        }
        if (payload == null) {
            return
        }

        if (payload.type != "transcript" || payload.text.isNullOrBlank()) {
            return
        }

        transcripts += TranscriptMessage(
            speaker = payload.speaker ?: "user",
            text = payload.text,
            timestampIso = payload.timestamp,
        )
        _state.value = VoiceConnectionState.Connected(transcripts.toList())
    }

    private fun openSignalingSocket(deviceId: String) {
        val url = buildWebSocketUrl(deviceId)
        val request = Request.Builder()
            .url(url)
            .build()

        val listener = object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: okhttp3.Response) {
                Log.i(TAG, "Signaling socket open")
                this@VoiceSessionController.webSocket = webSocket
                sendOfferIfReady()
                flushPendingCandidates()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleSignalingMessage(text)
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                handleSignalingMessage(bytes.utf8())
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "Signaling socket closing code=$code reason=$reason")
                webSocket.close(code, reason)
                stop()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: okhttp3.Response?) {
                Log.e(TAG, "Signaling socket failure", t)
                _state.value = VoiceConnectionState.Failed("Signaling failed: ${t.message}")
                stop()
            }
        }

        okHttpClient.newWebSocket(request, listener)
    }

    private fun handleSignalingMessage(payload: String) {
        val message = try {
            JSONObject(payload)
        } catch (error: JSONException) {
            Log.w(TAG, "Ignoring malformed signaling message: $payload")
            return
        }

        when (message.optString("type")) {
            "answer" -> {
                val sdp = message.optString("sdp")
                if (sdp.isNullOrBlank()) {
                    Log.w(TAG, "Answer missing SDP")
                    return
                }
                scope.launch {
                    try {
                        peerConnection?.setRemoteDescriptionAwait(
                            SessionDescription(SessionDescription.Type.ANSWER, sdp),
                        )
                    } catch (error: Exception) {
                        Log.e(TAG, "Failed to apply remote answer", error)
                        _state.value = VoiceConnectionState.Failed("Remote answer failed")
                        stop()
                    }
                }
            }

            "candidate" -> {
                val candidatePayload = message.optJSONObject("candidate") ?: return
                val candidate = candidatePayload.optString("candidate")
                val mid = candidatePayload.optString("sdpMid")
                val index = candidatePayload.optInt("sdpMLineIndex", -1)
                if (candidate.isNullOrBlank() || index < 0) {
                    return
                }
                val rtcCandidate = IceCandidate(mid, index, candidate)
                peerConnection?.addIceCandidate(rtcCandidate)
            }

            "bye" -> {
                Log.i(TAG, "Signaling BYE received")
                stop()
            }

            else -> Log.d(TAG, "Unhandled signaling payload: $payload")
        }
    }

    private fun buildWebSocketUrl(deviceId: String): String {
        val base = backendEnvironment.baseUrl().trim()
        val httpUrl = base.toHttpUrlOrNull()
            ?: throw IllegalStateException("Invalid backend base URL: $base")

        val scheme = if (httpUrl.scheme == "https") "wss" else "ws"
        return httpUrl.newBuilder()
            .scheme(scheme)
            .encodedPath("/ws/mobile/voice")
            .addQueryParameter("device_id", deviceId)
            .build()
            .toString()
    }

    private fun ensureFactory() {
        if (peerConnectionFactory != null) {
            return
        }

        val initOptions = PeerConnectionFactory.InitializationOptions.builder(context)
            .createInitializationOptions()
        PeerConnectionFactory.initialize(initOptions)

        val audioModule = createAudioDeviceModule()
        audioDeviceModule = audioModule

        val egl = EglBase.create()
        eglBase = egl

        val encoderFactory = DefaultVideoEncoderFactory(egl.eglBaseContext, true, true)
        val decoderFactory = DefaultVideoDecoderFactory(egl.eglBaseContext)

        peerConnectionFactory = PeerConnectionFactory.builder()
            .setAudioDeviceModule(audioModule)
            .setVideoEncoderFactory(encoderFactory)
            .setVideoDecoderFactory(decoderFactory)
            .createPeerConnectionFactory()
    }

    private fun createAudioDeviceModule(): AudioDeviceModule {
        val builder = JavaAudioDeviceModule.builder(context)
        builder.setUseHardwareAcousticEchoCanceler(true)
        builder.setUseHardwareNoiseSuppressor(true)
        builder.setAudioRecordErrorCallback(object : AudioRecordErrorCallback {
            override fun onWebRtcAudioRecordInitError(errorMessage: String?) {
                Log.e(TAG, "Audio record init error: $errorMessage")
            }

            override fun onWebRtcAudioRecordStartError(errorCode: JavaAudioDeviceModule.AudioRecordStartErrorCode?, errorMessage: String?) {
                Log.e(TAG, "Audio record start error: code=$errorCode message=$errorMessage")
            }

            override fun onWebRtcAudioRecordError(errorMessage: String?) {
                Log.e(TAG, "Audio record error: $errorMessage")
            }
        })
        builder.setAudioRecordStateCallback(object : AudioRecordStateCallback {
            override fun onWebRtcAudioRecordStart() {
                Log.d(TAG, "Audio record start")
            }

            override fun onWebRtcAudioRecordStop() {
                Log.d(TAG, "Audio record stop")
            }
        })
        builder.setAudioTrackErrorCallback(object : AudioTrackErrorCallback {
            override fun onWebRtcAudioTrackInitError(errorMessage: String?) {
                Log.e(TAG, "Audio track init error: $errorMessage")
            }

            override fun onWebRtcAudioTrackStartError(errorCode: JavaAudioDeviceModule.AudioTrackStartErrorCode?, errorMessage: String?) {
                Log.e(TAG, "Audio track start error: code=$errorCode message=$errorMessage")
            }

            override fun onWebRtcAudioTrackError(errorMessage: String?) {
                Log.e(TAG, "Audio track error: $errorMessage")
            }
        })
        builder.setAudioTrackStateCallback(object : AudioTrackStateCallback {
            override fun onWebRtcAudioTrackStart() {
                Log.d(TAG, "Audio track start")
            }

            override fun onWebRtcAudioTrackStop() {
                Log.d(TAG, "Audio track stop")
            }
        })
        return builder.createAudioDeviceModule()
    }

    private fun buildIceServers(servers: List<IceServerConfig>): List<PeerConnection.IceServer> {
        if (servers.isEmpty()) {
            return emptyList()
        }

        return servers.mapNotNull { config ->
            if (config.urls.isEmpty()) {
                return@mapNotNull null
            }
            val builder = PeerConnection.IceServer.builder(config.urls)
            if (!config.username.isNullOrBlank()) {
                builder.setUsername(config.username)
            }
            if (!config.credential.isNullOrBlank()) {
                builder.setPassword(config.credential)
            }
            builder.createIceServer()
        }
    }

    private fun cleanup() {
        try {
            webSocket?.send(JSONObject().put("type", "bye").toString())
        } catch (error: Exception) {
            Log.w(TAG, "Error sending BYE", error)
        }

        try {
            webSocket?.close(1000, "ended")
        } catch (error: Exception) {
            Log.w(TAG, "Error closing signaling socket", error)
        }
        webSocket = null

        transcriptsChannel?.close()
        transcriptsChannel?.dispose()
        transcriptsChannel = null

        controlChannel?.close()
        controlChannel?.dispose()
        controlChannel = null

        try {
            peerConnection?.close()
            peerConnection?.dispose()
        } catch (error: Exception) {
            Log.w(TAG, "Error closing peer connection", error)
        }
        peerConnection = null

        try {
            localAudioTrack?.dispose()
            audioSource?.dispose()
        } catch (error: Exception) {
            Log.w(TAG, "Error disposing audio", error)
        }
        localAudioTrack = null
        audioSource = null

        try {
            audioDeviceModule?.release()
        } catch (error: Exception) {
            Log.w(TAG, "Error releasing audio module", error)
        }
        audioDeviceModule = null

        try {
            peerConnectionFactory?.dispose()
        } catch (error: Exception) {
            Log.w(TAG, "Error disposing factory", error)
        }
        peerConnectionFactory = null

        try {
            eglBase?.release()
        } catch (error: Exception) {
            Log.w(TAG, "Error releasing EGL base", error)
        }
        eglBase = null

        iceGatheringDeferred?.cancel()
        iceGatheringDeferred = null
        pendingOffer = null
        pendingCandidates.clear()

        if (_state.value !is VoiceConnectionState.Failed) {
            _state.value = VoiceConnectionState.Idle
        }

        sessionJobActive.set(false)
    }

    private data class TranscriptPayload(
        @Json(name = "type") val type: String?,
        @Json(name = "speaker") val speaker: String?,
        @Json(name = "text") val text: String?,
        @Json(name = "timestamp") val timestamp: String?,
    )
}

private suspend fun PeerConnection.createOfferAwait(constraints: MediaConstraints): SessionDescription =
    suspendCancellableCoroutine { continuation ->
        this.createOffer(object : SdpObserverAdapter() {
            override fun onCreateSuccess(description: SessionDescription?) {
                if (description == null) {
                    continuation.resumeWithException(IllegalStateException("Offer failed: empty description"))
                    return
                }
                continuation.resume(description)
            }

            override fun onCreateFailure(error: String?) {
                continuation.resumeWithException(IllegalStateException("Offer failed: $error"))
            }
        }, constraints)
    }

private suspend fun PeerConnection.setLocalDescriptionAwait(description: SessionDescription) =
    suspendCancellableCoroutine<Unit> { continuation ->
        this.setLocalDescription(object : SdpObserverAdapter() {
            override fun onSetSuccess() {
                continuation.resume(Unit)
            }

            override fun onSetFailure(error: String?) {
                continuation.resumeWithException(IllegalStateException("Set local failed: $error"))
            }
        }, description)
    }

private suspend fun PeerConnection.setRemoteDescriptionAwait(description: SessionDescription) =
    suspendCancellableCoroutine<Unit> { continuation ->
        this.setRemoteDescription(object : SdpObserverAdapter() {
            override fun onSetSuccess() {
                continuation.resume(Unit)
            }

            override fun onSetFailure(error: String?) {
                continuation.resumeWithException(IllegalStateException("Set remote failed: $error"))
            }
        }, description)
    }

private open class SdpObserverAdapter : org.webrtc.SdpObserver {
    override fun onCreateSuccess(sessionDescription: SessionDescription?) {}
    override fun onSetSuccess() {}
    override fun onCreateFailure(error: String?) {}
    override fun onSetFailure(error: String?) {}
}
