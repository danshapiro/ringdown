package com.ringdown.mobile.voice

import android.app.Notification
import android.app.NotificationManager
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.R
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class VoiceSessionNotificationBuilderTest {

    private lateinit var context: Context
    private lateinit var builder: VoiceSessionNotificationBuilder

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        builder = VoiceSessionNotificationBuilder(context)
    }

    @Test
    fun connectedNotificationUsesConnectedCopy() {
        val notification = builder.build(VoiceConnectionState.Connected(emptyList()), isReconnecting = false)
        assertNotificationText(notification, context.getString(R.string.voice_notification_connected))
    }

    @Test
    fun connectingNotificationUsesConnectingCopy() {
        val notification = builder.build(VoiceConnectionState.Connecting, isReconnecting = false)
        assertNotificationText(notification, context.getString(R.string.voice_notification_connecting))
    }

    @Test
    fun reconnectingNotificationOverridesCopy() {
        val notification = builder.build(VoiceConnectionState.Connecting, isReconnecting = true)
        assertNotificationText(notification, context.getString(R.string.voice_notification_reconnecting))
    }

    private fun assertNotificationText(notification: Notification, expected: String) {
        builder.notify(notification)
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val shadow = Shadows.shadowOf(manager)
        val posted = shadow.allNotifications.lastOrNull()
        val text = posted?.extras?.getCharSequence(Notification.EXTRA_TEXT)?.toString()
        assertThat(text).isEqualTo(expected)
    }
}
