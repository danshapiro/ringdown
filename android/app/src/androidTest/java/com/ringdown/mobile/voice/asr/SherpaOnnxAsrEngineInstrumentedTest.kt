package com.ringdown.mobile.voice.asr

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.ringdown.mobile.BuildConfig
import com.ringdown.mobile.data.models.LocalModelInstaller
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Assume
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SherpaOnnxAsrEngineInstrumentedTest {

    @Test
    fun startAndStopDoesNotEmitErrors() = runBlocking {
        Assume.assumeTrue("Local audio alpha disabled", BuildConfig.ENABLE_LOCAL_AUDIO_ALPHA)
        val context = ApplicationProvider.getApplicationContext<Context>()
        val installer = LocalModelInstaller(context)
        val fakeAudio = object : AudioInputSource {
            override fun frames(sampleRate: Int, frameSize: Int): Flow<FloatArray> = flow {
                repeat(10) {
                    emit(FloatArray(frameSize))
                    delay(5)
                }
            }
        }

        val engine = SherpaOnnxAsrEngine(
            appContext = context,
            modelInstaller = installer,
            audioInputSource = fakeAudio,
            ioDispatcher = Dispatchers.IO,
        )

        val events = mutableListOf<AsrEvent>()
        val collection = launch { engine.events.collect { events += it } }

        engine.start()
        delay(250)
        engine.stop()
        collection.cancel()

        val errorEvents = events.filterIsInstance<AsrEvent.Error>()
        assertTrue("Expected no error events but found ${'$'}{errorEvents.size}", errorEvents.isEmpty())
    }
}
