package com.ringdown.mobile.voice

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.annotation.VisibleForTesting
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.ringdown.mobile.MainActivity
import com.ringdown.mobile.R
import com.ringdown.mobile.di.IoDispatcher
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.concurrent.atomic.AtomicBoolean
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

private const val VOICE_NOTIFICATION_ID = 0x71c
private const val VOICE_NOTIFICATION_CHANNEL_ID = "voice_session"

@VisibleForTesting
open class VoiceSessionNotificationObserver internal constructor(
    private val context: Context,
    private val voiceStates: StateFlow<VoiceConnectionState>,
    private val reconnectingStates: StateFlow<Boolean>,
    private val scope: CoroutineScope,
) {

    private val notificationManager = NotificationManagerCompat.from(context)
    private val channelCreated = AtomicBoolean(false)

    private val contentIntent: PendingIntent by lazy {
        val intent = Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        PendingIntent.getActivity(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private val hangUpIntent: PendingIntent by lazy {
        val intent = Intent(context, VoiceSessionActionReceiver::class.java)
            .setAction(VoiceSessionActionReceiver.ACTION_HANG_UP)
        PendingIntent.getBroadcast(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    init {
        scope.launch {
            voiceStates
                .combine(reconnectingStates) { state, reconnecting -> state to reconnecting }
                .collect { (state, reconnecting) ->
                    when (state) {
                        is VoiceConnectionState.Connected -> showNotification(state, reconnecting)
                        VoiceConnectionState.Connecting -> showNotification(state, reconnecting)
                        VoiceConnectionState.Idle,
                        is VoiceConnectionState.Failed -> hideNotification()
                    }
                }
        }
    }

    open fun initialize() {
        // Intentionally blank – calling this ensures the singleton is instantiated early.
    }

    private fun showNotification(
        state: VoiceConnectionState,
        isReconnecting: Boolean,
    ) {
        ensureChannel()
        val contentTextRes = when {
            isReconnecting -> R.string.voice_notification_reconnecting
            state is VoiceConnectionState.Connecting -> R.string.voice_notification_connecting
            else -> R.string.voice_notification_connected
        }
        val notification = NotificationCompat.Builder(context, VOICE_NOTIFICATION_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_voice_notification)
            .setContentTitle(context.getString(R.string.voice_notification_title))
            .setContentText(context.getString(contentTextRes))
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(contentIntent)
            .addAction(
                R.drawable.ic_voice_notification,
                context.getString(R.string.hang_up_button),
                hangUpIntent,
            )
            .build()
        notificationManager.notify(VOICE_NOTIFICATION_ID, notification)
    }

    private fun hideNotification() {
        notificationManager.cancel(VOICE_NOTIFICATION_ID)
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return
        }
        if (channelCreated.get()) {
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
        channelCreated.set(true)
    }
}

@Singleton
class VoiceSessionNotifier @Inject constructor(
    @ApplicationContext context: Context,
    voiceController: LocalVoiceSessionController,
    @IoDispatcher dispatcher: CoroutineDispatcher,
) : VoiceSessionNotificationObserver(
    context = context,
    voiceStates = voiceController.state,
    reconnectingStates = voiceController.reconnecting,
    scope = CoroutineScope(SupervisorJob() + dispatcher),
)
