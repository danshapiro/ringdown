package com.ringdown.mobile.voice

import android.content.Context
import android.os.Build
import android.util.Log
import com.ringdown.mobile.BuildConfig
import com.ringdown.mobile.di.IoDispatcher
import com.ringdown.mobile.di.MainDispatcher
import com.ringdown.mobile.domain.ControlMessage
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.time.Duration
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

interface ControlHarness {
    suspend fun handle(message: ControlMessage, audioBytes: ByteArray)
    fun onCallClientAttached(callClient: VoiceCallClient)
    fun onCallClientDetached()
    fun updateMediaProjection(token: android.media.projection.MediaProjection?)
}

@Singleton
class DefaultControlHarness @Inject constructor(
    @ApplicationContext private val context: Context,
    @IoDispatcher private val ioDispatcher: CoroutineDispatcher,
    @MainDispatcher private val mainDispatcher: CoroutineDispatcher,
) : ControlHarness {

    private val audioInjector = ControlAudioInjector(mainDispatcher, CoroutineScope(Dispatchers.Default))
    private val responseRecorder = DebugControlResponseRecorder(context, ioDispatcher)

    override suspend fun handle(message: ControlMessage, audioBytes: ByteArray) {
        if (!BuildConfig.ENABLE_TEST_CONTROL_HARNESS) {
            return
        }

        val promptFile = withContext(ioDispatcher) {
            val outputDir = File(context.filesDir, "control-harness")
            if (!outputDir.exists() && !outputDir.mkdirs()) {
                Log.w(TAG, "Unable to create control harness directory at ${outputDir.absolutePath}")
                return@withContext null
            }

            val outputFile = File(outputDir, "${message.messageId}.wav")
            outputFile.writeBytes(audioBytes)
            Log.i(
                TAG,
                "Saved control prompt ${message.promptId} (${audioBytes.size} bytes) to ${outputFile.absolutePath}",
            )
            outputFile
        }

        val captureJob = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            responseRecorder.startCapture(message.messageId, CAPTURE_DURATION)
        } else {
            null
        }

        audioInjector.inject(message, audioBytes)

        captureJob?.let { job ->
            val responseFile = job.await()
            if (responseFile != null) {
                Log.i(TAG, "Captured control response for ${message.promptId} at ${responseFile.absolutePath}")
            } else {
                Log.w(TAG, "No control response captured for ${message.promptId}")
            }
        }

        if (promptFile == null) {
            Log.w(TAG, "Prompt file write failed for ${message.promptId}")
        }
    }

    override fun onCallClientAttached(callClient: VoiceCallClient) {
        audioInjector.attach(callClient)
    }

    override fun onCallClientDetached() {
        audioInjector.detach()
        responseRecorder.cancelOngoingCapture()
    }

    override fun updateMediaProjection(token: android.media.projection.MediaProjection?) {
        responseRecorder.updateMediaProjection(token)
    }

    companion object {
        private const val TAG = "ControlHarness"
        private val CAPTURE_DURATION: Duration = Duration.ofSeconds(5)
    }
}
