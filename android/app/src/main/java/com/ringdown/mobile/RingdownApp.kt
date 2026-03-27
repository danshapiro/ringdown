package com.ringdown.mobile

import android.app.Application
import com.ringdown.mobile.voice.VoiceSessionForegroundCoordinator
import dagger.hilt.android.HiltAndroidApp
import javax.inject.Inject

@HiltAndroidApp
class RingdownApp : Application() {

    @Inject
    lateinit var voiceSessionCoordinator: VoiceSessionForegroundCoordinator

    override fun onCreate() {
        super.onCreate()
        // Ensure the foreground service hooks are active even when UI is backgrounded.
        voiceSessionCoordinator.initialize()
    }
}
