package com.ringdown.mobile.voice.asr

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.util.Log
import androidx.annotation.VisibleForTesting
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.Dispatchers

interface AudioInputSource {
    fun frames(sampleRate: Int, frameSize: Int): Flow<FloatArray>
}

@Singleton
class MicrophoneAudioInputSource @Inject constructor() : AudioInputSource {

    override fun frames(sampleRate: Int, frameSize: Int): Flow<FloatArray> = callbackFlow {
        val minBuffer = AudioRecord.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer <= 0) {
            throw IllegalStateException("Unsupported microphone configuration (sampleRate=$sampleRate)")
        }

        val bufferSize = maxOf(minBuffer, frameSize * 4)
        val audioRecord = buildAudioRecord(sampleRate, bufferSize)

        if (audioRecord.state != AudioRecord.STATE_INITIALIZED) {
            audioRecord.release()
            throw IllegalStateException("Failed to initialize AudioRecord (state=${audioRecord.state})")
        }

        val shortBuffer = ShortArray(frameSize)
        audioRecord.startRecording()
        Log.i(TAG, "Microphone audio source started (sampleRate=$sampleRate, frameSize=$frameSize)")

        try {
            while (true) {
                val read = audioRecord.read(shortBuffer, 0, shortBuffer.size)
                if (read <= 0) {
                    continue
                }
                val floats = FloatArray(read)
                for (index in 0 until read) {
                    floats[index] = (shortBuffer[index] / 32768.0f).coerceIn(-1.0f, 1.0f)
                }
                trySend(floats)
            }
        } catch (throwable: Throwable) {
            Log.e(TAG, "Microphone input loop terminated", throwable)
            close(throwable)
        } finally {
            try {
                audioRecord.stop()
            } catch (_: IllegalStateException) {
                // ignore
            }
            audioRecord.release()
            Log.i(TAG, "Microphone audio source stopped")
            close()
        }
    }.flowOn(Dispatchers.IO)

    @VisibleForTesting
    internal fun buildAudioRecord(sampleRate: Int, bufferSize: Int): AudioRecord {
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            AudioRecord.Builder()
                .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setSampleRate(sampleRate)
                        .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .build(),
                )
                .setBufferSizeInBytes(bufferSize)
        } else {
            @Suppress("DEPRECATION")
            return AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferSize,
            )
        }

        return builder.build()
    }

    companion object {
        private const val TAG = "MicrophoneAudioSource"
    }
}
