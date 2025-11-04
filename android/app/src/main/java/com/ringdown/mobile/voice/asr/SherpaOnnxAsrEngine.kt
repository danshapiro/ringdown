package com.ringdown.mobile.voice.asr

import android.content.Context
import android.util.Log
import androidx.annotation.VisibleForTesting
import com.k2fsa.sherpa.onnx.EndpointConfig
import com.k2fsa.sherpa.onnx.EndpointRule
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OnlineRecognizer
import com.k2fsa.sherpa.onnx.OnlineRecognizerConfig
import com.k2fsa.sherpa.onnx.OnlineRecognizerResult
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import com.k2fsa.sherpa.onnx.OnlineLMConfig
import com.k2fsa.sherpa.onnx.OnlineCtcFstDecoderConfig
import com.k2fsa.sherpa.onnx.HomophoneReplacerConfig
import com.k2fsa.sherpa.onnx.OnlineNeMoCtcModelConfig
import com.k2fsa.sherpa.onnx.OnlineParaformerModelConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import com.k2fsa.sherpa.onnx.OnlineToneCtcModelConfig
import com.k2fsa.sherpa.onnx.OnlineZipformer2CtcModelConfig
import com.ringdown.mobile.data.models.LocalModelInstaller
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Singleton
class SherpaOnnxAsrEngine @Inject constructor(
    @ApplicationContext private val appContext: Context,
    private val modelInstaller: LocalModelInstaller,
    private val audioInputSource: AudioInputSource,
    @com.ringdown.mobile.di.IoDispatcher private val ioDispatcher: CoroutineDispatcher,
) : LocalAsrEngine {

    private val scope = CoroutineScope(SupervisorJob() + ioDispatcher)
    private val _events = MutableSharedFlow<AsrEvent>(
        extraBufferCapacity = 32,
        onBufferOverflow = BufferOverflow.SUSPEND,
    )
    override val events: SharedFlow<AsrEvent> = _events

    private val running = AtomicBoolean(false)
    private val utteranceCounter = AtomicLong(0)

    private var recognizer: OnlineRecognizer? = null
    private var stream: OnlineStream? = null
    private var audioJob: kotlinx.coroutines.Job? = null
    private var decodeJob: kotlinx.coroutines.Job? = null
    private var currentUtteranceId: String = nextUtteranceId()
    private var lastPartialText: String = ""

    override suspend fun start() = withContext(ioDispatcher) {
        if (!running.compareAndSet(false, true)) {
            Log.w(TAG, "ASR engine already running")
            return@withContext
        }

        try {
            val modelDirectory = modelInstaller.ensureSherpaStreamingAsrModel()
            val recognizerConfig = buildRecognizerConfig(modelDirectory)
            // Models are installed under the app's files directory with absolute paths,
            // so we must avoid passing an AssetManager reference (sherpa-onnx expects
            // a null assetManager when loading from the filesystem).
            val recognizer = OnlineRecognizer(null, recognizerConfig)
            val stream = recognizer.createStream("")

            this@SherpaOnnxAsrEngine.recognizer = recognizer
            this@SherpaOnnxAsrEngine.stream = stream
            currentUtteranceId = nextUtteranceId()
            lastPartialText = ""

            val audioFlow = audioInputSource.frames(SAMPLE_RATE, FRAME_SIZE)
            audioJob = scope.launch {
                collectAudioFrames(audioFlow, stream)
            }
            decodeJob = scope.launch {
                decodeLoop(recognizer, stream)
            }
            Log.i(TAG, "SherpaOnnx ASR engine started")
        } catch (error: Exception) {
            running.set(false)
            Log.e(TAG, "Failed to start ASR engine", error)
            _events.emit(AsrEvent.Error(error))
        }
    }

    override suspend fun stop() = withContext(ioDispatcher) {
        if (!running.compareAndSet(true, false)) {
            return@withContext
        }

        try {
            audioJob?.cancelAndJoin()
        } catch (_: CancellationException) {
            // ignore
        } finally {
            audioJob = null
        }
        try {
            decodeJob?.cancelAndJoin()
        } catch (_: CancellationException) {
            // ignore
        } finally {
            decodeJob = null
        }

        runCatching { stream?.inputFinished() }
        runCatching { stream?.release() }
        runCatching { recognizer?.release() }
        recognizer = null
        stream = null
        currentUtteranceId = nextUtteranceId()
        lastPartialText = ""
        Log.i(TAG, "SherpaOnnx ASR engine stopped")
    }

    private suspend fun collectAudioFrames(
        audioFrames: kotlinx.coroutines.flow.Flow<FloatArray>,
        stream: OnlineStream,
    ) {
        try {
            audioFrames.collect { frame ->
                if (frame.isEmpty()) return@collect
                stream.acceptWaveform(frame, SAMPLE_RATE)
            }
        } catch (cancel: CancellationException) {
            throw cancel
        } catch (error: Throwable) {
            if (running.get()) {
                Log.e(TAG, "Audio capture failure", error)
                _events.emit(AsrEvent.Error(error))
            }
        }
    }

    private suspend fun decodeLoop(
        recognizer: OnlineRecognizer,
        stream: OnlineStream,
    ) {
        try {
            while (scope.isActive && running.get()) {
                if (recognizer.isReady(stream)) {
                    recognizer.decode(stream)
                    handlePartialResult(recognizer.getResult(stream))
                } else {
                    delay(DECODE_IDLE_DELAY_MS)
                }

                if (recognizer.isEndpoint(stream)) {
                    finaliseCurrentUtterance(recognizer.getResult(stream))
                    recognizer.reset(stream)
                    currentUtteranceId = nextUtteranceId()
                    lastPartialText = ""
                }
            }
        } catch (cancel: CancellationException) {
            throw cancel
        } catch (error: Throwable) {
            if (running.get()) {
                Log.e(TAG, "Decoder failure", error)
                _events.emit(AsrEvent.Error(error))
            }
        }
    }

    private suspend fun handlePartialResult(result: OnlineRecognizerResult) {
        val text = result.text.trim()
        if (text.isBlank() || text == lastPartialText) {
            return
        }
        lastPartialText = text
        _events.emit(AsrEvent.Partial(utteranceId = currentUtteranceId, text = text))
    }

    private suspend fun finaliseCurrentUtterance(result: OnlineRecognizerResult) {
        val text = result.text.trim()
        if (text.isNotEmpty()) {
            _events.emit(AsrEvent.Final(utteranceId = currentUtteranceId, text = text))
        } else if (lastPartialText.isNotEmpty()) {
            _events.emit(AsrEvent.Final(utteranceId = currentUtteranceId, text = lastPartialText))
        }
    }

    @VisibleForTesting
    internal fun buildRecognizerConfig(modelDir: File): OnlineRecognizerConfig {
        val encoder = modelDir.resolve("encoder-epoch-99-avg-1.int8.onnx")
        val decoder = modelDir.resolve("decoder-epoch-99-avg-1.int8.onnx")
        val joiner = modelDir.resolve("joiner-epoch-99-avg-1.int8.onnx")
        val tokens = modelDir.resolve("tokens.txt")

        require(encoder.exists()) { "Missing encoder model (${encoder.absolutePath})" }
        require(decoder.exists()) { "Missing decoder model (${decoder.absolutePath})" }
        require(joiner.exists()) { "Missing joiner model (${joiner.absolutePath})" }
        require(tokens.exists()) { "Missing tokens file (${tokens.absolutePath})" }

        val featureConfig = FeatureConfig(
            SAMPLE_RATE,
            FEATURE_DIM,
            0.0f,
        )

        val modelConfig = OnlineModelConfig(
            OnlineTransducerModelConfig(
                encoder.absolutePath,
                decoder.absolutePath,
                joiner.absolutePath,
            ),
            OnlineParaformerModelConfig("", ""),
            OnlineZipformer2CtcModelConfig(""),
            OnlineNeMoCtcModelConfig(""),
            OnlineToneCtcModelConfig(""),
            tokens.absolutePath,
            Runtime.getRuntime().availableProcessors().coerceAtLeast(2),
            false,
            "cpu",
            "zipformer",
            "",
            "",
        )

        val endpointConfig = EndpointConfig(
            EndpointRule(
                mustContainNonSilence = true,
                minTrailingSilence = 1.2f,
                minUtteranceLength = 1.5f,
            ),
            EndpointRule(
                mustContainNonSilence = false,
                minTrailingSilence = 0.8f,
                minUtteranceLength = 4.0f,
            ),
            EndpointRule(
                mustContainNonSilence = false,
                minTrailingSilence = 2.4f,
                minUtteranceLength = 0.0f,
            ),
        )

        return OnlineRecognizerConfig(
            featureConfig,
            modelConfig,
            OnlineLMConfig("", 0.0f),
            OnlineCtcFstDecoderConfig("", 0),
            HomophoneReplacerConfig("", "", ""),
            endpointConfig,
            true,
            "greedy_search",
            4,
            "",
            0.0f,
            "",
            "",
            0.0f,
        )
    }

    private fun nextUtteranceId(): String = "utt-${utteranceCounter.incrementAndGet()}"

    companion object {
        private const val TAG = "SherpaOnnxAsrEngine"
        private const val SAMPLE_RATE = 16_000
        private const val FEATURE_DIM = 80
        private const val FRAME_SIZE = 1600
        private const val DECODE_IDLE_DELAY_MS = 20L
    }
}
