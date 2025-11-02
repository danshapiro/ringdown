package com.ringdown.mobile

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.models.LocalModelInstaller
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
    fun ensurePiperModelCopiesAssets() {
        val modelDir = installer.ensurePiperModel()
        val sentinel = modelDir.resolve("en_US-amy-low.onnx")
        assertThat(sentinel.exists()).isTrue()
        assertThat(sentinel.length()).isGreaterThan(0L)
    }
}
