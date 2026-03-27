package com.ringdown.mobile.voice

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class VoiceSessionActionReceiver : BroadcastReceiver() {

    @Inject
    lateinit var voiceController: LocalVoiceSessionController

    override fun onReceive(context: Context?, intent: Intent?) {
        if (intent?.action == ACTION_HANG_UP) {
            voiceController.stop()
        }
    }

    companion object {
        const val ACTION_HANG_UP = "com.ringdown.mobile.voice.ACTION_HANG_UP"
    }
}
