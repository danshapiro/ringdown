package com.ringdown.mobile.voice

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.os.Build
import android.os.SystemClock
import android.util.Log
import androidx.annotation.RequiresApi
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.time.Duration
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

internal interface ControlResponseRecorder {
    fun startCapture(messageId: String, duration: Duration): kotlinx.coroutines.Deferred<File?>
    fun updateMediaProjection(token: MediaProjection?)
    fun cancelOngoingCapture()
}

internal class DebugControlResponseRecorder(
    private val context: Context,
    private val ioDispatcher: CoroutineDispatcher,
) : ControlResponseRecorder {

    private val scope = CoroutineScope(Dispatchers.Default)
    private val projectionRef = AtomicReference<MediaProjection?>()
    private val captureMutex = Mutex()
    private val activeCapture = AtomicReference<kotlinx.coroutines.Deferred<File?>?>(null)

    override fun startCapture(messageId: String, duration: Duration): kotlinx.coroutines.Deferred<File?> {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            return CompletableDeferred(null)
        }
        val projection = projectionRef.get()
        if (projection == null) {
            Log.w(TAG, "MediaProjection unavailable; skipping response capture for $messageId")
            return CompletableDeferred(null)
        }

        val deferred = scope.async {
            val result = captureMutex.withLock {
                recordPlayback(projection, messageId, duration)
            }
            result
        }

        activeCapture.getAndSet(deferred)?.cancel()
        return deferred
    }

    override fun updateMediaProjection(token: MediaProjection?) {
        projectionRef.set(token)
    }

    override fun cancelOngoingCapture() {
        activeCapture.getAndSet(null)?.cancel()
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private suspend fun recordPlayback(
        projection: MediaProjection,
        messageId: String,
        duration: Duration,
    ): File? = withContext(ioDispatcher) {
        val outputDir = File(context.filesDir, "control-harness/responses")
        if (!outputDir.exists() && !outputDir.mkdirs()) {
            Log.w(TAG, "Unable to create response directory at ${outputDir.absolutePath}")
            return@withContext null
        }

        val buffer = ByteArrayOutputStream()
        val audioRecord = buildAudioRecord(projection) ?: return@withContext null

        try {
            val bufferSize = audioRecord.bufferSizeInFrames * BYTES_PER_SAMPLE
            val tempBuffer = ByteArray(bufferSize)
            audioRecord.startRecording()
            val deadline = SystemClock.elapsedRealtime() + duration.toMillis()
            while (SystemClock.elapsedRealtime() < deadline) {
                val read = audioRecord.read(tempBuffer, 0, tempBuffer.size)
                if (read > 0) {
                    buffer.write(tempBuffer, 0, read)
                }
            }
        } catch (error: Exception) {
            Log.w(TAG, "Error capturing playback audio for $messageId", error)
        } finally {
            try {
                audioRecord.stop()
            } catch (_: Exception) {
                // ignore
            }
            audioRecord.release()
        }

        val pcmBytes = buffer.toByteArray()
        if (pcmBytes.isEmpty()) {
            Log.w(TAG, "No playback audio captured for $messageId")
            return@withContext null
        }

        val outputFile = File(outputDir, "${messageId}_response.wav")
        FileOutputStream(outputFile).use { stream ->
            stream.write(buildWavHeader(pcmBytes.size))
            stream.write(pcmBytes)
        }
        outputFile
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private fun buildAudioRecord(projection: MediaProjection): AudioRecord? {
        val config = AudioPlaybackCaptureConfiguration.Builder(projection)
            .addMatchingUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
            .build()

        val minBuffer = AudioRecord.getMinBufferSize(
            SAMPLE_RATE_HZ,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer == AudioRecord.ERROR || minBuffer == AudioRecord.ERROR_BAD_VALUE) {
            Log.w(TAG, "Invalid min buffer size for playback capture")
            return null
        }

        return AudioRecord.Builder()
            .setAudioPlaybackCaptureConfig(config)
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(SAMPLE_RATE_HZ)
                    .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                    .build(),
            )
            .setBufferSizeInBytes(minBuffer * 2)
            .build()
    }

    private fun buildWavHeader(pcmSize: Int): ByteArray {
        val dataSize = pcmSize
        val totalDataLen = dataSize + 36
        val byteRate = SAMPLE_RATE_HZ * CHANNEL_COUNT * BYTES_PER_SAMPLE

        val buffer = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        buffer.put("RIFF".toByteArray())
        buffer.putInt(totalDataLen)
        buffer.put("WAVE".toByteArray())
        buffer.put("fmt ".toByteArray())
        buffer.putInt(16) // PCM chunk size
        buffer.putShort(1) // audio format PCM
        buffer.putShort(CHANNEL_COUNT.toShort())
        buffer.putInt(SAMPLE_RATE_HZ)
        buffer.putInt(byteRate)
        buffer.putShort((CHANNEL_COUNT * BYTES_PER_SAMPLE).toShort())
        buffer.putShort(BITS_PER_SAMPLE.toShort())
        buffer.put("data".toByteArray())
        buffer.putInt(dataSize)
        return buffer.array()
    }

    companion object {
        private const val TAG = "ControlPlaybackRecorder"
        private const val SAMPLE_RATE_HZ = 48_000
        private const val CHANNEL_COUNT = 1
        private const val BITS_PER_SAMPLE = 16
        private const val BYTES_PER_SAMPLE = 2
    }
}
