package com.ringdown.mobile.voice

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.domain.ControlMessage
import java.time.Instant
import java.util.Base64
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ControlAudioInjectorTest {

    private val dispatcher = StandardTestDispatcher()
    private val scope = TestScope(dispatcher)
    private val injector = ControlAudioInjector(dispatcher, scope)

    @Test
    fun injectStreamsPcmFramesToCustomTrack() = runTest(dispatcher) {
        val callClient = FakeVoiceCallClient()
        injector.attach(callClient)

        val payload = ControlMessage(
            messageId = "msg-1",
            promptId = "prompt-1",
            audioBase64 = TEST_WAV_BASE64,
            sampleRateHz = 16_000,
            channels = 1,
            format = "wav",
            metadata = emptyMap(),
            enqueuedAtIso = Instant.EPOCH.toString(),
        )

        injector.inject(payload, Base64.getDecoder().decode(TEST_WAV_BASE64))
        advanceUntilIdle()

        assertThat(callClient.consumedFrames).isNotEmpty()
        assertThat(callClient.removed).isTrue()
        injector.detach()
    }

    private class FakeVoiceCallClient : VoiceCallClient {
        private var consumer: RecordingConsumer? = null
        private var source: co.daily.model.customtrack.CustomAudioSource? = null
        val consumedFrames = mutableListOf<ShortArray>()
        var removed: Boolean = false

        override fun attachListener(listener: co.daily.CallClientListener) {}
        override fun detachListener(listener: co.daily.CallClientListener) {}
        override fun join(session: com.ringdown.mobile.domain.ManagedVoiceSession, onError: (String?) -> Unit) {
            onError(null)
        }
        override fun leave(onComplete: () -> Unit) = onComplete()
        override fun release() {}

        override fun addCustomAudioTrack(
            name: co.daily.model.customtrack.CustomTrackName,
            source: co.daily.model.customtrack.CustomAudioSource,
            onResult: (co.daily.model.RequestResult?) -> Unit,
        ) {
            this.source = source
            val recordingConsumer = RecordingConsumer(consumedFrames)
            consumer = recordingConsumer
            onResult(null)
            source.attachFrameConsumer(recordingConsumer)
        }

        override fun removeCustomAudioTrack(
            name: co.daily.model.customtrack.CustomTrackName,
            onResult: (co.daily.model.RequestResult?) -> Unit,
        ) {
            removed = true
            source?.detachFrameConsumer()
            onResult(null)
        }
    }

    private class RecordingConsumer(
        private val sink: MutableList<ShortArray>,
    ) : co.daily.model.customtrack.CustomAudioFrameConsumer() {
        override fun sendFrame(
            format: co.daily.model.customtrack.AudioFrameFormat,
            buffer: ShortArray,
            offset: Int,
            length: Int,
        ) {
            val chunk = buffer.copyOfRange(offset, offset + length)
            sink += chunk
        }
    }

    companion object {
        private const val TEST_WAV_BASE64 =
            "UklGRmQGAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YUAGAAAAAP8VVStjP5RRS2Edbq53t33wf1t+" +
                "CnklcNxjmlTYQhwv8hkEBPrtdNgfxJaxYKH1k8WJJIM1gA2BroUBjrGZaKi8uTDNIuL39woOsSNHODRL6VvTaZh0" +
                "7HuXf2J/ZXvDc7RoelqPSXg2xCEKDPf1L+BXyxO4+KZ/mBWNHIXVgFGAkoOMihGVvaIss+bFYdr37wYG6xv2MIxEG" +
                "1YhZRJxq3mrfu1/Sn3ydhZtAGD/T6E9cCkEFP39B+jH0tu+5axsnd+QmIfwgRGA+IGZh9eQbJ3prNy+w9IH6P39Ax" +
                "R0KaI9/E/6Xx9t9XZGfeJ/t36veQ9xGGUjVo5E9DDoGwcG9u9h2urFKbO5ohKVlYqPg0qA1oAkhRSNeZj4phe4V8s" +
                "t4Pb1CQzDIXw2kUl2Wq9ozXNoe1x/jn/3e5t0z2nhWztLSTiwIwkO9vch4jHNwrlkqK2ZAo63hQiBLoAlg82J85Nb" +
                "oZaxI8R12PjtBATxGRov3UKbVNdjIXATeV1+6n+wfbh3H25HYY5RaT9WK/4V////6ajUnsByrq2e3ZFTiFOCCYCeg" +
                "faG4o8fnGGrKL3n0Azm+/sGEogn4DtvTqBeBGw4duR8y3/sfkx6CHJPZpNXP0bTMt0dCAj18UrctsfOtB6kJJZiix" +
                "aEc4CVgJSEPoxTl4GlbLaIyT3e9fMJCtAfpTTtRw1ZgGfjcuF6Mn+vf2Z8b3X1akNd0EwXOqElCBD5+RbkBs9xu+a" +
                "p5prijlCGWIEdgKuCCYnrkgeg+q9bwo/W/esCAvkXNy0fQRxTmGIgb194EH73fwd+X3gnb5piFlMfQTot+hcCAv3r" +
                "jtZYwgGwCaDnkgCJtoIhgFSBRYbsjuqa5KlsuwnPGOT5+QkQnyUUOtBMSl3yaml1Z3y3fzB/2nrkcodnDlnoR6U00" +
                "h8JCvXzPt6FyWm2iKVVlzmMjYSggHaAEYRZiy2WIaTLtLDHTtz28QgI3x3QMjxGlFdXZgJyR3ruftV/4HwxdgVspl" +
                "5uTt07iCcHEvr7Dubo0CS9XqsmnOSP8YaYgRKAVYJOiNaRtp50rpvApdQB6gAA/hVaK2Q/i1FKYSlusXeqfe1/Z34" +
                "OeRtw2WOhVNtCGC/xGQUE+O132CLEkbFZofqTzokfgyqAEYG4hf2NqJlrqMO5L80g4vf3CQ6xI084NEveW9JppnTu" +
                "e4l/X39ye8Zzqmh3WpZJezbBIQoM9vUu4FrLF7jypniYG40lhc+ASICYg5aKDZW1oi+z68Vg2vbvBgbnG/Ywk0QbV" +
                "hVlE3G7eat+3n9Jff92GG32X/5Ppz1xKQIU/f0F6MXS4L7prGad2JCgh/iBCYDvgaCH35BnneOs4L7I0gbo/f0DFH" +
                "AppD0FUPhfFG32dlR94n+nfq95HXEZZRlWjkT6MOkbBgb3717a6MUvs7yiCpWQipmDUIDNgB6FHI1/mPKmErhayy/" +
                "g9vUKDMIhdzaTSX5arGjBc2t7an+Mf+l7nXTbaeFbMktJOLUjCg739yLiLM3AuWyosJn3jbOFE4E0gBuDx4n7k1+h" +
                "kLEfxHfY+e0EBPMZGC/XQp5U4GMdcAl5YX72f6x9rHciblJhjVFhP1crABYAAAHqqdSWwHGuuJ7gkUeIT4IVgKKB7" +
                "IbejyicZKsiveXQDub7+wcSiyfcO2lOpF4MbDJ22nzRf/d+SHr9cVJmm1c9Rs4y3h0JCPbxT9y2x8W0HqQwlmSLCI" +
                "RxgKOAl4QyjFCXiaVutoPJPN728wkK0h+oNOhHB1mHZ+ty23opf7V/cHxqde1qRl3WTBU6niUJEPj5F+QLz3G73Kn" +
                "nmvCOUYZIgR2AuYIKieCSBaADsF3Ci9b86wIC+Bc8LSNBFlOTYihvZngHfu9/D35neCBvk2IaUyRBOC34FwIC++uP" +
                "1l7CALD/n+mSDYm1ghKAVIFUhu2O3prkqXO7Cc8U5Pn5CBCeJRk600xCXe5qc3VtfK5/Kn/jeupygGcHWexHqDTQH" +
                "wkK9fM73ofJcLaGpUuXPIyahJ2AaIAThGeLLJYWpMu0uMdO3PXxCAjdHc8yQ0aXV05m/nFRevJ+yn/bfDp2CmyfXm" +
                "lO4DuLJwUS+/sN5uPQJ71lqyOc2o/1hqSBD4BIglGI4pG0nmuunMCq1ADq"
    }
}
