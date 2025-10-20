package com.ringdown.domain.usecase

import android.content.Context
import androidx.core.content.ContextCompat
import com.ringdown.voice.VoiceForegroundService
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class StartVoiceSessionUseCase @Inject constructor(
    @ApplicationContext private val context: Context
) {
    operator fun invoke(deviceId: String) {
        val intent = VoiceForegroundService.createStartIntent(context, deviceId)
        ContextCompat.startForegroundService(context, intent)
    }
}
