package com.ringdown.mobile.voice

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.VoiceSessionDataSource
import com.ringdown.mobile.domain.ControlMessage
import com.ringdown.mobile.domain.ManagedVoiceSession
import com.squareup.moshi.Moshi
import java.time.Duration
import java.time.Instant
import java.util.ArrayDeque
import java.util.Base64
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class VoiceSessionControllerControlHarnessTest {

    @Test
    fun controlMessageDeliveredToHarness() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val repository = ControlAwareRepository()
        repository.enqueueControlMessage(
            ControlMessage(
                messageId = "control-1",
                promptId = "prompt-123",
                audioBase64 = TEST_WAV_BASE64,
                sampleRateHz = 16_000,
                channels = 1,
                format = "wav",
                metadata = emptyMap(),
                enqueuedAtIso = "2025-10-30T00:00:00Z",
            ),
        )
        val harness = RecordingHarness()
        val controller = VoiceSessionController(
            repository = repository,
            callClientFactory = object : VoiceCallClientFactory {
                override fun create(): VoiceCallClient = object : VoiceCallClient {
                    override fun attachListener(listener: co.daily.CallClientListener) {}
                    override fun detachListener(listener: co.daily.CallClientListener) {}
                    override fun join(session: ManagedVoiceSession, onError: (String?) -> Unit) {
                        onError(null)
                    }
                    override fun leave(onComplete: () -> Unit) = onComplete()
                    override fun release() {}
                }
            },
            moshi = Moshi.Builder().build(),
            controlHarness = harness,
            dispatcher = dispatcher,
            mainDispatcher = dispatcher,
            minRefreshLead = Duration.ofMinutes(5),
            nowProvider = InstantProvider { Instant.EPOCH },
        )
        harness.onHandled = { controller.stop() }

        controller.start(deviceId = "device-1", agent = "agent-a")
        runCurrent()

        assertThat(repository.fetchHistory).containsExactly("session-1" to "control-key-1")
        assertThat(harness.messages).hasSize(1)
        val recorded = harness.messages.first()
        assertThat(recorded.messageId).isEqualTo("control-1")
        assertThat(recorded.promptId).isEqualTo("prompt-123")
        val expectedHeader = Base64.getDecoder().decode(TEST_WAV_BASE64).sliceArray(0 until 4)
        assertThat(recorded.audioBytes.sliceArray(0 until 4)).isEqualTo(expectedHeader)

        controller.stop()
        runCurrent()
    }

    private class ControlAwareRepository : VoiceSessionDataSource {
        private val controlQueue: ArrayDeque<ControlMessage?> = ArrayDeque()
        val fetchHistory: MutableList<Pair<String, String>> = mutableListOf()

        fun enqueueControlMessage(message: ControlMessage?) {
            controlQueue.add(message)
        }

        override suspend fun createSession(deviceId: String, agent: String?): ManagedVoiceSession {
            return ManagedVoiceSession(
                sessionId = "session-1",
                agent = agent ?: "agent-a",
                roomUrl = "https://example.invalid/room",
                accessToken = "token-xyz",
                expiresAt = Instant.EPOCH.plusSeconds(600),
                pipelineSessionId = "pipeline-1",
                metadata = mapOf(
                    "control" to mapOf(
                        "key" to "control-key-1",
                        "pollPath" to "/v1/mobile/managed-av/control/next",
                    ),
                ),
                greeting = null,
            )
        }

        override suspend fun fetchControlMessage(sessionId: String, controlKey: String): ControlMessage? {
            fetchHistory += sessionId to controlKey
            return if (controlQueue.isEmpty()) null else controlQueue.removeFirst()
        }
    }

    private class RecordingHarness : ControlHarness {
        data class Recorded(val messageId: String, val promptId: String, val audioBytes: ByteArray)

        val messages: MutableList<Recorded> = mutableListOf()
        var onHandled: (() -> Unit)? = null

        override suspend fun handle(message: ControlMessage, audioBytes: ByteArray) {
            messages += Recorded(
                messageId = message.messageId,
                promptId = message.promptId,
                audioBytes = audioBytes,
            )
            onHandled?.invoke()
        }
    }

    companion object {
        private val TEST_WAV_BASE64 =
            (
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
                )
    }
}
