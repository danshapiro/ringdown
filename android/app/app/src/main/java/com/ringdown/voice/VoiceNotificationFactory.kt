package com.ringdown.voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.graphics.Color
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.ringdown.R
import com.ringdown.domain.model.VoiceSessionState
import javax.inject.Inject
import javax.inject.Singleton
import dagger.hilt.android.qualifiers.ApplicationContext

@Singleton
class VoiceNotificationFactory @Inject constructor(
    @ApplicationContext private val context: Context
) {

    private val notificationManager: NotificationManagerCompat =
        NotificationManagerCompat.from(context)

    fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return

        val channel = NotificationChannel(
            CHANNEL_ID,
            context.getString(R.string.voice_notification_channel_name),
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = context.getString(R.string.voice_notification_channel_description)
            enableLights(false)
            enableVibration(false)
            lightColor = Color.WHITE
        }
        manager.createNotificationChannel(channel)
    }

    fun buildForState(
        state: VoiceSessionState,
        deviceId: String?
    ): Notification {
        val title = when (state) {
            VoiceSessionState.Connecting -> context.getString(R.string.voice_notification_connecting_title)
            is VoiceSessionState.Active -> context.getString(R.string.voice_notification_active_title)
            is VoiceSessionState.Reconnecting -> context.getString(R.string.voice_notification_reconnect_title)
            is VoiceSessionState.Error -> context.getString(R.string.voice_notification_error_title)
            VoiceSessionState.Disconnected -> context.getString(R.string.voice_notification_disconnected_title)
        }

        val message = when (state) {
            VoiceSessionState.Connecting -> context.getString(R.string.voice_notification_connecting_message)
            is VoiceSessionState.Active -> context.getString(R.string.voice_notification_active_message, deviceId ?: "device")
            is VoiceSessionState.Reconnecting -> context.getString(R.string.voice_notification_reconnect_message)
            is VoiceSessionState.Error -> state.message
            VoiceSessionState.Disconnected -> context.getString(R.string.voice_notification_disconnected_message)
        }

        val hangUpIntent = VoiceForegroundService.createHangUpIntent(context)
        val pendingHangUp = PendingIntent.getService(
            context,
            REQUEST_CODE_HANG_UP,
            hangUpIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification_voice)
            .setOngoing(state !is VoiceSessionState.Error && state != VoiceSessionState.Disconnected)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentTitle(title)
            .setContentText(message)
            .addAction(
                R.drawable.ic_notification_voice,
                context.getString(R.string.voice_notification_hang_up_action),
                pendingHangUp
            )
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
    }

    fun notify(state: VoiceSessionState, deviceId: String?) {
        notificationManager.notify(NOTIFICATION_ID, buildForState(state, deviceId))
    }

    fun cancel() {
        notificationManager.cancel(NOTIFICATION_ID)
    }

    companion object {
        const val NOTIFICATION_ID = 41
        const val CHANNEL_ID = "voice_session"
        private const val REQUEST_CODE_HANG_UP = 1002
    }
}
