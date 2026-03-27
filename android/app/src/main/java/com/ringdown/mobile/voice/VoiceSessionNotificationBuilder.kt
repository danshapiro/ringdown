package com.ringdown.mobile.voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.ringdown.mobile.MainActivity
import com.ringdown.mobile.R
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.concurrent.atomic.AtomicBoolean
import javax.inject.Inject
import javax.inject.Singleton

const val VOICE_NOTIFICATION_ID = 0x71c
private const val VOICE_NOTIFICATION_CHANNEL_ID = "voice_session"

@Singleton
class VoiceSessionNotificationBuilder @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    private val notificationManager = NotificationManagerCompat.from(context)
    private val channelCreated = AtomicBoolean(false)

    fun build(state: VoiceConnectionState, isReconnecting: Boolean): Notification {
        ensureChannel()
        val contentTextRes = when {
            isReconnecting -> R.string.voice_notification_reconnecting
            state is VoiceConnectionState.Connecting -> R.string.voice_notification_connecting
            else -> R.string.voice_notification_connected
        }
        return NotificationCompat.Builder(context, VOICE_NOTIFICATION_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_voice_notification)
            .setContentTitle(context.getString(R.string.voice_notification_title))
            .setContentText(context.getString(contentTextRes))
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(contentIntent())
            .addAction(
                R.drawable.ic_voice_notification,
                context.getString(R.string.hang_up_button),
                hangUpIntent(),
            )
            .build()
    }

    fun notify(notification: Notification) {
        notificationManager.notify(VOICE_NOTIFICATION_ID, notification)
    }

    fun cancel() {
        notificationManager.cancel(VOICE_NOTIFICATION_ID)
    }

    private fun contentIntent(): PendingIntent {
        val intent = Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        return PendingIntent.getActivity(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun hangUpIntent(): PendingIntent {
        val intent = Intent(context, VoiceSessionActionReceiver::class.java)
            .setAction(VoiceSessionActionReceiver.ACTION_HANG_UP)
        return PendingIntent.getBroadcast(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return
        }
        if (!channelCreated.compareAndSet(false, true)) {
            return
        }
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        val name = context.getString(R.string.voice_notification_channel)
        val channel = NotificationChannel(
            VOICE_NOTIFICATION_CHANNEL_ID,
            name,
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            enableLights(false)
            enableVibration(false)
            setShowBadge(false)
            description = name
        }
        manager.createNotificationChannel(channel)
    }
}
