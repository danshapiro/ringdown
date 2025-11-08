package com.ringdown.mobile.voice

import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import com.ringdown.mobile.di.IoDispatcher
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.concurrent.atomic.AtomicBoolean
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

@Singleton
class VoiceSessionForegroundCoordinator @Inject constructor(
    @ApplicationContext private val context: Context,
    private val voiceController: LocalVoiceSessionController,
    @IoDispatcher dispatcher: CoroutineDispatcher,
) {

    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val started = AtomicBoolean(false)
    private val serviceRunning = AtomicBoolean(false)

    fun initialize() {
        if (!started.compareAndSet(false, true)) {
            return
        }
        scope.launch {
            voiceController.state.collectLatest { state ->
                val shouldRun = state is VoiceConnectionState.Connecting || state is VoiceConnectionState.Connected
                if (shouldRun) {
                    startServiceIfNeeded()
                } else {
                    stopServiceIfNeeded()
                }
            }
        }
    }

    private fun startServiceIfNeeded() {
        if (serviceRunning.compareAndSet(false, true)) {
            ContextCompat.startForegroundService(context, serviceIntent())
        }
    }

    private fun stopServiceIfNeeded() {
        if (serviceRunning.get()) {
            context.stopService(serviceIntent())
            serviceRunning.set(false)
        }
    }

    private fun serviceIntent(): Intent = Intent(context, VoiceSessionForegroundService::class.java)

    fun onServiceStopped() {
        serviceRunning.set(false)
    }
}
