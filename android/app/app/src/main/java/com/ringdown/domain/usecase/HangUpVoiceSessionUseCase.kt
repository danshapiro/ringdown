package com.ringdown.domain.usecase

import android.content.Context
import androidx.core.content.ContextCompat
import com.ringdown.voice.VoiceForegroundService
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class HangUpVoiceSessionUseCase @Inject constructor(
    @ApplicationContext private val context: Context
) {
    operator fun invoke() {
        val intent = VoiceForegroundService.createHangUpIntent(context)
        ContextCompat.startForegroundService(context, intent)
    }
}
