package com.ringdown.mobile.voice

import android.app.Service
import android.content.Intent
import android.os.IBinder
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

@AndroidEntryPoint
class VoiceSessionForegroundService : LifecycleService() {

    @Inject
    lateinit var voiceController: LocalVoiceSessionController

    @Inject
    lateinit var notificationBuilder: VoiceSessionNotificationBuilder

    @Inject
    lateinit var coordinator: VoiceSessionForegroundCoordinator

    private var foregroundStarted = false
    private var monitorJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        monitorJob = lifecycleScope.launch {
            voiceController.state
                .combine(voiceController.reconnecting) { state, reconnecting -> state to reconnecting }
                .collect { (state, reconnecting) ->
                    when (state) {
                        is VoiceConnectionState.Connected,
                        VoiceConnectionState.Connecting -> showNotification(state, reconnecting)
                        VoiceConnectionState.Idle,
                        is VoiceConnectionState.Failed -> stopForegroundService()
                    }
                }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return Service.START_STICKY
    }

    override fun onDestroy() {
        monitorJob?.cancel()
        stopForegroundService()
        coordinator.onServiceStopped()
        super.onDestroy()
    }

    override fun onBind(intent: Intent): IBinder? {
        return super.onBind(intent)
    }

    private fun showNotification(state: VoiceConnectionState, reconnecting: Boolean) {
        val notification = notificationBuilder.build(state, reconnecting)
        if (!foregroundStarted) {
            startForeground(VOICE_NOTIFICATION_ID, notification)
            foregroundStarted = true
        } else {
            notificationBuilder.notify(notification)
        }
    }

    private fun stopForegroundService() {
        if (foregroundStarted) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            foregroundStarted = false
        }
        notificationBuilder.cancel()
        stopSelf()
    }
}
