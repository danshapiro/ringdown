package com.ringdown.voice

import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.content.pm.ServiceInfo
import androidx.core.app.ServiceCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import com.ringdown.domain.model.VoiceSessionState
import com.ringdown.domain.usecase.VoiceSessionController
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

@AndroidEntryPoint
class VoiceForegroundService : LifecycleService() {

    @Inject
    lateinit var voiceSessionController: VoiceSessionController

    @Inject
    lateinit var notificationFactory: VoiceNotificationFactory

    private var collectionJob: Job? = null
    private var currentDeviceId: String? = null
    private var hasSeenActiveState = false

    override fun onCreate() {
        super.onCreate()
        notificationFactory.ensureChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> handleStart(intent.getStringExtra(EXTRA_DEVICE_ID))
            ACTION_HANG_UP -> handleHangUp()
        }
        return Service.START_STICKY
    }

    override fun onBind(intent: Intent): IBinder? {
        super.onBind(intent)
        return null
    }

    private fun handleStart(deviceId: String?) {
        currentDeviceId = deviceId
        hasSeenActiveState = false
        val notification = notificationFactory.buildForState(
            VoiceSessionState.Connecting,
            currentDeviceId
        )
        val foregroundType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        } else {
            0
        }
        ServiceCompat.startForeground(
            this,
            VoiceNotificationFactory.NOTIFICATION_ID,
            notification,
            foregroundType
        )

        if (collectionJob == null) {
            collectionJob = lifecycleScope.launch {
                voiceSessionController.state.collect { state ->
                    notificationFactory.notify(state, currentDeviceId)
                    when (state) {
                        VoiceSessionState.Connecting,
                        is VoiceSessionState.Active,
                        is VoiceSessionState.Reconnecting -> {
                            hasSeenActiveState = true
                        }
                        VoiceSessionState.Disconnected -> {
                            if (hasSeenActiveState) {
                                stopSelfSafely()
                            }
                        }
                        is VoiceSessionState.Error -> {
                            hasSeenActiveState = false
                            stopSelfSafely()
                        }
                    }
                }
            }
        }
        voiceSessionController.startSession()
    }

    private fun handleHangUp() {
        voiceSessionController.hangUp()
        stopSelfSafely()
    }

    private fun stopSelfSafely() {
        notificationFactory.cancel()
        collectionJob?.cancel()
        collectionJob = null
        stopForeground(ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        collectionJob?.cancel()
        super.onDestroy()
    }

    companion object {
        private const val ACTION_START = "com.ringdown.voice.action.START"
        private const val ACTION_HANG_UP = "com.ringdown.voice.action.HANG_UP"
        private const val EXTRA_DEVICE_ID = "extra_device_id"

        fun createStartIntent(context: Context, deviceId: String): Intent =
            Intent(context, VoiceForegroundService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_DEVICE_ID, deviceId)
            }

        fun createHangUpIntent(context: Context): Intent =
            Intent(context, VoiceForegroundService::class.java).apply {
                action = ACTION_HANG_UP
            }
    }
}
