package com.ringdown.mobile

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

@RunWith(AndroidJUnit4::class)
class LocalModelInstallerInstrumentedTest {

    private lateinit var installer: LocalModelInstaller

    @Before
    fun setUp() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        installer = LocalModelInstaller(context)
        installer.clearAllInstalledModels()
    }

    @After
    fun tearDown() {
        installer.clearAllInstalledModels()
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
    fun ensureSherpaStreamingModelCopiesAssets() = runBlocking {
        val modelDir = installer.ensureSherpaStreamingAsrModel()
        val sentinel = modelDir.resolve("encoder-epoch-99-avg-1.int8.onnx")
        assertThat(sentinel.exists()).isTrue()
        assertThat(sentinel.length()).isGreaterThan(0L)

        val metadata = installer.installedMetadata(LocalModelId.SHERPA_STREAMING_EN_20M)
        assertThat(metadata).isNotNull()
        assertThat(metadata!!.payloads.map { it.relativePath })
            .containsAtLeast(
                "encoder-epoch-99-avg-1.int8.onnx",
                "decoder-epoch-99-avg-1.int8.onnx",
                "joiner-epoch-99-avg-1.int8.onnx",
            )
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
