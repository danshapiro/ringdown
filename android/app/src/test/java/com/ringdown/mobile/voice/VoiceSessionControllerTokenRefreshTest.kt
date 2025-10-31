package com.ringdown.mobile.voice

import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.VoiceSessionDataSource
import com.ringdown.mobile.domain.ManagedVoiceSession
import com.ringdown.mobile.domain.ControlMessage
import com.squareup.moshi.Moshi
import java.time.Duration
import java.time.Instant
import java.util.ArrayDeque
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class VoiceSessionControllerTokenRefreshTest {

    @Test
    fun tokenRefreshTriggersSecondJoin() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val fakeClock = FakeClock(Instant.EPOCH)
        val ttlQueue = ArrayDeque<Duration>().apply {
            add(Duration.ofMillis(50))
            add(Duration.ofMillis(50))
        }
        val repository = QueueVoiceSessionDataSource(fakeClock, ttlQueue)
        val callClient = RecordingVoiceCallClient()
        val factory = object : VoiceCallClientFactory {
            override fun create(): VoiceCallClient = callClient
        }
        val controller = VoiceSessionController(
            repository = repository,
            callClientFactory = factory,
            moshi = Moshi.Builder().build(),
            controlHarness = object : ControlHarness {
                override suspend fun handle(message: ControlMessage, audioBytes: ByteArray) = Unit
                override fun onCallClientAttached(callClient: VoiceCallClient) {}
                override fun onCallClientDetached() {}
                override fun updateMediaProjection(token: android.media.projection.MediaProjection?) {}
            },
            dispatcher = dispatcher,
            mainDispatcher = dispatcher,
            minRefreshLead = Duration.ofMillis(10),
            nowProvider = InstantProvider { fakeClock.now() },
        )

        controller.start(deviceId = "device-1", agent = "agent-a")
        runCurrent()
        assertThat(callClient.joinCount).isEqualTo(1)

        fakeClock.advance(Duration.ofMillis(60))
        advanceTimeBy(60)
        runCurrent()

        assertThat(callClient.joinCount).isEqualTo(2)
    }

    private class QueueVoiceSessionDataSource(
        private val clock: FakeClock,
        private val ttls: ArrayDeque<Duration>,
    ) : VoiceSessionDataSource {
        private var counter = 0

        override suspend fun createSession(deviceId: String, agent: String?): ManagedVoiceSession {
            return if (ttls.isEmpty()) {
                error("No sessions available for $deviceId")
            } else {
                counter += 1
                clock.newSession("session-$counter", ttls.removeFirst())
            }
        }

        override suspend fun fetchControlMessage(sessionId: String, controlKey: String): ControlMessage? = null
    }

    private class RecordingVoiceCallClient : VoiceCallClient {
        var joinCount: Int = 0
        private var listener: co.daily.CallClientListener? = null

        override fun attachListener(listener: co.daily.CallClientListener) {
            this.listener = listener
        }

        override fun detachListener(listener: co.daily.CallClientListener) {
            if (this.listener === listener) {
                this.listener = null
            }
        }

        override fun join(session: ManagedVoiceSession, onError: (String?) -> Unit) {
            joinCount += 1
            onError(null)
        }

        override fun leave(onComplete: () -> Unit) {
            onComplete()
        }

        override fun release() {}

        override fun addCustomAudioTrack(
            name: co.daily.model.customtrack.CustomTrackName,
            source: co.daily.model.customtrack.CustomAudioSource,
            onResult: (co.daily.model.RequestResult?) -> Unit,
        ) {
            onResult(null)
        }

        override fun removeCustomAudioTrack(
            name: co.daily.model.customtrack.CustomTrackName,
            onResult: (co.daily.model.RequestResult?) -> Unit,
        ) {
            onResult(null)
        }
    }

    private class FakeClock(start: Instant) {
        private var current: Instant = start

        fun now(): Instant = current

        fun advance(duration: Duration) {
            current = current.plus(duration)
        }

        fun newSession(sessionId: String, ttl: Duration): ManagedVoiceSession {
            return ManagedVoiceSession(
                sessionId = sessionId,
                agent = "agent-a",
                roomUrl = "https://example.invalid/room",
                accessToken = "token-$sessionId",
                expiresAt = current.plus(ttl),
                pipelineSessionId = null,
                metadata = emptyMap(),
                greeting = null,
            )
        }

    }
}
