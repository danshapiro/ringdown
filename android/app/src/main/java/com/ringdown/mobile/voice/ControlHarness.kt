package com.ringdown.mobile.voice

import android.content.Context
import android.util.Log
import com.ringdown.mobile.BuildConfig
import com.ringdown.mobile.domain.ControlMessage
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

interface ControlHarness {
    suspend fun handle(message: ControlMessage, audioBytes: ByteArray)
}

@Singleton
class DefaultControlHarness @Inject constructor(
    @ApplicationContext private val context: Context,
) : ControlHarness {

    override suspend fun handle(message: ControlMessage, audioBytes: ByteArray) {
        if (!BuildConfig.ENABLE_TEST_CONTROL_HARNESS) {
            return
        }

        withContext(Dispatchers.IO) {
            val outputDir = File(context.filesDir, "control-harness")
            if (!outputDir.exists() && !outputDir.mkdirs()) {
                Log.w(TAG, "Unable to create control harness directory at ${outputDir.absolutePath}")
                return@withContext
            }

            val outputFile = File(outputDir, "${message.messageId}.wav")
            outputFile.writeBytes(audioBytes)
            Log.i(
                TAG,
                "Saved control prompt ${message.promptId} (${audioBytes.size} bytes) to ${outputFile.absolutePath}",
            )
        }
    }

    companion object {
        private const val TAG = "ControlHarness"
    }
}
