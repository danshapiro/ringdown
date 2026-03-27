package com.ringdown.mobile

import android.content.ContextWrapper
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.models.LocalModelInstaller
import com.ringdown.mobile.data.models.LocalModelInstaller.LocalModelId
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class LocalModelInstallerInstrumentedTest {

    private lateinit var installer: LocalModelInstaller
    private lateinit var tempFilesDir: File

    @Before
    fun setUp() {
        val baseContext = InstrumentationRegistry.getInstrumentation().targetContext
        tempFilesDir = File(baseContext.cacheDir, "local-model-installer-test")
        if (tempFilesDir.exists()) {
            tempFilesDir.deleteRecursively()
        }
        tempFilesDir.mkdirs()
        val context = object : ContextWrapper(baseContext) {
            override fun getFilesDir(): File = tempFilesDir
        }
        installer = LocalModelInstaller(context)
        installer.clearAllInstalledModels()
    }

    @After
    fun tearDown() {
        if (!::installer.isInitialized) {
            return
        }
        installer.clearAllInstalledModels()
        if (this::tempFilesDir.isInitialized && tempFilesDir.exists()) {
            tempFilesDir.deleteRecursively()
        }
    }

    @Test
    fun ensurePiperModelCopiesAssets() = runBlocking {
        val modelDir = installer.ensurePiperModel()
        val sentinel = modelDir.resolve("en_US-amy-low.onnx")
        assertThat(sentinel.exists()).isTrue()
        assertThat(sentinel.length()).isGreaterThan(0L)

        val metadata = installer.installedMetadata(LocalModelId.PIPER_AMY_LOW)
        assertThat(metadata).isNotNull()
        assertThat(metadata!!.payloads).isNotEmpty()
    }

    @Test
    fun ensureSherpaStreamingModelCopiesAssets() = runBlocking<Unit> {
        val modelDir = installer.ensureSherpaStreamingAsrModel()
        val sentinel = modelDir.resolve("encoder-epoch-99-avg-1.int8.onnx")
        assertThat(sentinel.exists()).isTrue()
        assertThat(sentinel.length()).isGreaterThan(0L)

        val metadata = installer.installedMetadata(LocalModelId.SHERPA_STREAMING_EN_20M)
        assertThat(metadata).isNotNull()
        val payloadPaths = metadata!!.payloads.map { it.relativePath }.toSet()
        val expected = setOf(
            "encoder-epoch-99-avg-1.int8.onnx",
            "decoder-epoch-99-avg-1.int8.onnx",
            "joiner-epoch-99-avg-1.int8.onnx",
        )
        assertThat(payloadPaths).containsAtLeastElementsIn(expected)
        Unit
    }

    @Test
    fun reinstallWhenPayloadMissing() = runBlocking {
        val modelDir = installer.ensurePiperModel()
        val sentinel = modelDir.resolve("en_US-amy-low.onnx")
        assertThat(sentinel.delete()).isTrue()

        val restoredDir = installer.ensurePiperModel()
        val restoredSentinel = restoredDir.resolve("en_US-amy-low.onnx")
        assertThat(restoredSentinel.exists()).isTrue()
        assertThat(restoredSentinel.length()).isGreaterThan(0L)
    }
}
