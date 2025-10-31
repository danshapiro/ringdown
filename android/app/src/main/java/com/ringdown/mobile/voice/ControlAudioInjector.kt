package com.ringdown.mobile.voice

import android.util.Log
import co.daily.model.RequestResult
import co.daily.model.customtrack.AudioFrameFormat
import co.daily.model.customtrack.CustomAudioFrameConsumer
import co.daily.model.customtrack.CustomAudioSource
import co.daily.model.customtrack.CustomTrackName
import com.ringdown.mobile.domain.ControlMessage
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.max
import kotlin.math.min
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout

internal class ControlAudioInjector(
    private val mainDispatcher: CoroutineDispatcher,
    private val playbackScope: CoroutineScope = CoroutineScope(Dispatchers.Default),
) {

    private val mutex = Mutex()
    private var callClient: VoiceCallClient? = null

    fun attach(callClient: VoiceCallClient) {
        this.callClient = callClient
    }

    fun detach() {
        this.callClient = null
    }

    suspend fun inject(message: ControlMessage, audioBytes: ByteArray) {
        val client = callClient ?: run {
            Log.w(TAG, "No Daily CallClient available; skipping control audio for ${message.promptId}")
            return
        }

        val payload = WavPayload.parse(audioBytes) ?: run {
            Log.w(TAG, "Invalid WAV payload for control message ${message.promptId}")
            return
        }

        mutex.withLock {
            val trackName = CustomTrackName(TRACK_NAME)
            val source = WavCustomAudioSource(payload, playbackScope)
            val addResult = CompletableDeferred<RequestResult?>()

            withContext(mainDispatcher) {
                client.addCustomAudioTrack(trackName, source) { result ->
                    if (!addResult.isCompleted) {
                        addResult.complete(result)
                    }
                }
            }

            val requestResult = addResult.await()
            if (requestResult?.isError == true) {
                Log.w(
                    TAG,
                    "Daily rejected control audio track for ${message.promptId}: ${requestResult.error?.msg}",
                )
                return@withLock
            }

            val completed = source.awaitCompletion()

            withContext(mainDispatcher) {
                client.removeCustomAudioTrack(trackName) { /* no-op */ }
            }

            if (!completed) {
                Log.w(TAG, "Timed out waiting for control audio playback for ${message.promptId}")
            }
        }
    }

    private data class WavPayload(
        val sampleRateHz: Int,
        val channels: Int,
        val bitsPerSample: Int,
        val pcmData: ShortArray,
    ) {
        companion object {
            private const val RIFF = "RIFF"
            private const val WAVE = "WAVE"
            private const val FMT = "fmt "
            private const val DATA = "data"

            fun parse(bytes: ByteArray): WavPayload? {
                if (bytes.size < 44) {
                    return null
                }

                val buffer = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
                val riff = buffer.readAscii(4)
                buffer.int // total chunk size
                val wave = buffer.readAscii(4)
                if (riff != RIFF || wave != WAVE) {
                    return null
                }

                var sampleRate = 16_000
                var channels = 1
                var bitsPerSample = 16
                var dataOffset = -1
                var dataSize = 0

                while (buffer.remaining() >= 8) {
                    val chunkId = buffer.readAscii(4)
                    val chunkSize = buffer.int
                    if (chunkSize < 0 || chunkSize > buffer.remaining()) {
                        return null
                    }
                    when (chunkId) {
                        FMT -> {
                            val audioFormat = buffer.short
                            if (audioFormat.toInt() != 1) {
                                return null
                            }
                            channels = buffer.short.toInt()
                            sampleRate = buffer.int
                            buffer.int // byte rate
                            buffer.short // block align
                            bitsPerSample = buffer.short.toInt()
                            val remaining = chunkSize - 16
                            if (remaining > 0) {
                                buffer.position(buffer.position() + remaining)
                            }
                        }
                        DATA -> {
                            dataOffset = buffer.position()
                            dataSize = chunkSize
                            buffer.position(buffer.position() + chunkSize)
                        }
                        else -> {
                            buffer.position(buffer.position() + chunkSize)
                        }
                    }
                    if (chunkSize % 2 == 1) {
                        buffer.position(buffer.position() + 1)
                    }
                }

                if (dataOffset < 0 || dataSize <= 0) {
                    return null
                }
                if (bitsPerSample != 16) {
                    return null
                }

                val dataBuffer = ByteBuffer.wrap(bytes, dataOffset, dataSize).order(ByteOrder.LITTLE_ENDIAN)
                val samples = dataSize / 2
                val pcm = ShortArray(samples)
                for (index in 0 until samples) {
                    pcm[index] = dataBuffer.short
                }

                return WavPayload(
                    sampleRateHz = sampleRate,
                    channels = channels,
                    bitsPerSample = bitsPerSample,
                    pcmData = pcm,
                )
            }
        }
    }

    private class WavCustomAudioSource(
        private val payload: WavPayload,
        private val scope: CoroutineScope,
    ) : CustomAudioSource() {

        private val completion = CompletableDeferred<Unit>()

        override fun attachFrameConsumer(consumer: CustomAudioFrameConsumer) {
            scope.launchPlayback {
                streamToConsumer(consumer)
            }
        }

        override fun detachFrameConsumer() {
            if (!completion.isCompleted) {
                completion.complete(Unit)
            }
        }

        suspend fun awaitCompletion(timeoutMs: Long = 10_000): Boolean {
            return try {
                withTimeout(timeoutMs) {
                    completion.await()
                    true
                }
            } catch (error: Throwable) {
                false
            }
        }

        private fun CoroutineScope.launchPlayback(block: suspend CoroutineScope.() -> Unit) {
            launch {
                try {
                    block()
                } finally {
                    if (!completion.isCompleted) {
                        completion.complete(Unit)
                    }
                }
            }
        }

        private suspend fun streamToConsumer(consumer: CustomAudioFrameConsumer) {
            val framesPerChunk = calculateFramesPerChunk()
            val format = AudioFrameFormat(
                payload.bitsPerSample,
                payload.sampleRateHz,
                payload.channels,
            )
            var cursor = 0
            while (cursor < payload.pcmData.size) {
                val chunkSize = min(framesPerChunk, payload.pcmData.size - cursor)
                consumer.sendFrame(format, payload.pcmData, cursor, chunkSize)
                cursor += chunkSize
                delay(FRAME_INTERVAL_MS)
            }
        }

        private fun calculateFramesPerChunk(): Int {
            val samplesPerChannel = max(1, payload.sampleRateHz * FRAME_INTERVAL_MS.toInt() / 1000)
            return samplesPerChannel * payload.channels
        }
    }

    companion object {
        private const val TAG = "ControlAudioInjector"
        private const val TRACK_NAME = "ringdown-control"
        private const val FRAME_INTERVAL_MS = 20L
    }
}

private fun ByteBuffer.readAscii(length: Int): String {
    val array = ByteArray(length)
    get(array)
    return array.decodeToString()
}
