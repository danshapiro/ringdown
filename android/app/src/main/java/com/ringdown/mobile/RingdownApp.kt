package com.ringdown.mobile

import android.app.Application
import com.ringdown.mobile.voice.VoiceSessionNotifier
import dagger.hilt.android.HiltAndroidApp
import javax.inject.Inject

@HiltAndroidApp
class RingdownApp : Application() {

    @Inject
    lateinit var voiceSessionNotifier: VoiceSessionNotifier

    override fun onCreate() {
        super.onCreate()
        // Touch the notifier so its flow collectors attach as soon as the app starts.
        voiceSessionNotifier.initialize()
    }
}
