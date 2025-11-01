package com.ringdown.ttsdemo

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Bundle
import android.util.Log
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.k2fsa.sherpa.onnx.OfflineTts
import com.k2fsa.sherpa.onnx.getOfflineTtsConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import kotlin.math.max

class MainActivity : AppCompatActivity() {

    private lateinit var statusView: TextView
    private var ttsEngine: OfflineTts? = null
    private var audioTrack: AudioTrack? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        statusView = findViewById(R.id.status_view)

        lifecycleScope.launch {
            runDemo()
        }
    }

    private suspend fun runDemo() = withContext(Dispatchers.IO) {
        try {
            updateStatus("Copying TTS model assets…")
            Log.d(TAG, "Copying assets for model: $MODEL_DIR")
            val (modelDir, dataDir) = ensureAssets()
            Log.d(TAG, "Local asset paths -> model=$modelDir data=$dataDir")

            updateStatus("Initialising sherpa-onnx engine…")
        val config = getOfflineTtsConfig(
            modelDir = modelDir,
            modelName = MODEL_NAME,
            acousticModelName = "",
            vocoder = "",
            voices = "",
                lexicon = "",
                dataDir = dataDir,
                dictDir = "",
                ruleFsts = "",
                ruleFars = "",
                numThreads = null,
                isKitten = false,
            )

            val engine = OfflineTts(assets, config)
            ttsEngine = engine
            Log.d(TAG, "OfflineTts initialised (sampleRate=${engine.sampleRate()}, speakers=${engine.numSpeakers()})")

            updateStatus("Synthesising sample paragraph…")
            val audio = engine.generate(SAMPLE_TEXT, 0, 1.0f)
            Log.d(TAG, "Synthesis complete (samples=${audio.samples.size}, sampleRate=${audio.sampleRate})")

            updateStatus("Playing locally generated audio…")
            playAudio(audio.samples, audio.sampleRate)

            updateStatus("Playback complete. Demo finished.")
        } catch (t: Throwable) {
            Log.e(TAG, "Failed to run TTS demo", t)
            updateStatus("Error: ${t.message ?: "unknown"}")
        }
    }

    private fun playAudio(samples: FloatArray, sampleRate: Int) {
        if (samples.isEmpty()) {
            return
        }

        val minBufferBytes = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_FLOAT,
        )
        val bufferCapacity = if (minBufferBytes > 0) {
            max(minBufferBytes, samples.size * Float.SIZE_BYTES)
        } else {
            samples.size * Float.SIZE_BYTES
        }

        val attributes = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()

        val format = AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_FLOAT)
            .setSampleRate(sampleRate)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .build()

        val track = AudioTrack(
            attributes,
            format,
            bufferCapacity,
            AudioTrack.MODE_STREAM,
            AudioManager.AUDIO_SESSION_ID_GENERATE,
        )
        audioTrack = track
        track.play()

        var offset = 0
        while (offset < samples.size) {
            val written = track.write(samples, offset, samples.size - offset, AudioTrack.WRITE_BLOCKING)
            if (written <= 0) {
                break
            }
            offset += written
        }

        while (track.playState == AudioTrack.PLAYSTATE_PLAYING && track.playbackHeadPosition < samples.size) {
            try {
                Thread.sleep(20)
            } catch (_: InterruptedException) {
                break
            }
        }

        track.stop()
        track.flush()
    }

    private fun ensureAssets(): Pair<String, String> {
        val dataTarget = File(filesDir, DATA_SUBDIR)
        if (!dataTarget.exists()) {
            Log.d(TAG, "Copying data assets for $DATA_SUBDIR")
            copyAssetTree(DATA_SUBDIR, filesDir)
        } else {
            Log.d(TAG, "Data assets already present at ${dataTarget.absolutePath}")
        }

        val dataDirPath = dataTarget.absolutePath
        return MODEL_DIR to dataDirPath
    }

    private fun copyAssetTree(assetPath: String, destinationRoot: File) {
        val children = try {
            assets.list(assetPath)
        } catch (io: IOException) {
            Log.e(TAG, "Unable to list asset path: $assetPath", io)
            null
        }

        if (children == null || children.isEmpty()) {
            copyAssetFile(assetPath, destinationRoot)
            return
        }

        val targetDir = File(destinationRoot, assetPath)
        if (!targetDir.exists()) {
            targetDir.mkdirs()
        }
        for (child in children) {
            val childPath = if (assetPath.isEmpty()) child else "$assetPath/$child"
            copyAssetTree(childPath, destinationRoot)
        }
    }

    private fun copyAssetFile(assetPath: String, destinationRoot: File) {
        val target = File(destinationRoot, assetPath)
        if (target.exists()) {
            return
        }
        target.parentFile?.mkdirs()
        try {
            assets.open(assetPath).use { input ->
                target.outputStream().use { output ->
                    input.copyTo(output)
                }
            }
            Log.d(TAG, "Copied asset file: $assetPath -> ${target.absolutePath}")
        } catch (io: IOException) {
            Log.e(TAG, "Failed to copy asset file: $assetPath", io)
        }
    }

    private suspend fun updateStatus(message: String) = withContext(Dispatchers.Main) {
        statusView.text = message
    }

    override fun onDestroy() {
        try {
            audioTrack?.stop()
            audioTrack?.release()
        } catch (_: IllegalStateException) {
            // ignored – happens if track already stopped
        }
        audioTrack = null
        ttsEngine?.release()
        ttsEngine = null
        super.onDestroy()
    }

    companion object {
        private const val TAG = "SherpaTtsDemo"
        private const val MODEL_DIR = "vits-piper-en_US-amy-low"
        private const val DATA_SUBDIR = "vits-piper-en_US-amy-low/espeak-ng-data"
        private const val MODEL_NAME = "en_US-amy-low.onnx"
        private const val SAMPLE_TEXT =
            "Hi Dan, this is the Ringdown T T S smoke test. " +
                "We are rendering this entire paragraph locally on the handset using the Sherpa Onnx engine. " +
                "If you can hear this message clearly, the offline synthesis pipeline is working. " +
                "Thanks for listening."
    }
}
