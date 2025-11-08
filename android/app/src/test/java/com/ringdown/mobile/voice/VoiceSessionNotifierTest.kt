package com.ringdown.mobile.voice

import android.app.Notification
import android.app.NotificationManager
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.R
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runCurrent
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.Shadows

@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class VoiceSessionNotifierTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun connectedStateShowsNotification() {
        val voiceStates = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Idle)
        val reconnecting = MutableStateFlow(false)
        val scope = TestScope(UnconfinedTestDispatcher())
        VoiceSessionNotificationObserver(
            context = context,
            voiceStates = voiceStates,
            reconnectingStates = reconnecting,
            scope = scope,
        ).initialize()

        voiceStates.value = VoiceConnectionState.Connected(emptyList())
        scope.runCurrent()

        val notification = latestNotification()
        assertThat(notification).isNotNull()
        val text = notification!!.extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()
        assertThat(text).isEqualTo(context.getString(R.string.voice_notification_connected))
    }

    @Test
    fun idleStateClearsNotification() {
        val voiceStates = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Idle)
        val reconnecting = MutableStateFlow(false)
        val scope = TestScope(UnconfinedTestDispatcher())
        VoiceSessionNotificationObserver(
            context = context,
            voiceStates = voiceStates,
            reconnectingStates = reconnecting,
            scope = scope,
        ).initialize()

        voiceStates.value = VoiceConnectionState.Connected(emptyList())
        scope.runCurrent()
        voiceStates.value = VoiceConnectionState.Idle
        scope.runCurrent()

        val notification = latestNotification()
        assertThat(notification).isNull()
    }

    @Test
    fun reconnectingStateUsesReconnectCopy() {
        val voiceStates = MutableStateFlow<VoiceConnectionState>(VoiceConnectionState.Connecting)
        val reconnecting = MutableStateFlow(true)
        val scope = TestScope(UnconfinedTestDispatcher())
        VoiceSessionNotificationObserver(
            context = context,
            voiceStates = voiceStates,
            reconnectingStates = reconnecting,
            scope = scope,
        ).initialize()

        scope.runCurrent()

        val notification = latestNotification()
        val text = notification!!.extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()
        assertThat(text).isEqualTo(context.getString(R.string.voice_notification_reconnecting))
    }

    private fun latestNotification(): Notification? {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val shadow = Shadows.shadowOf(manager)
        return shadow.allNotifications.lastOrNull()
    }
}
