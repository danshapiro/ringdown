package com.ringdown.data.voice

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.withContext
import dagger.hilt.android.qualifiers.ApplicationContext

@Singleton
class AudioRouteController @Inject constructor(
    @ApplicationContext private val context: Context,
    private val dispatcher: CoroutineDispatcher
) {

    private val audioManager: AudioManager =
        context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    suspend fun acquireVoiceRoute() = withContext(dispatcher) {
        audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        audioManager.isSpeakerphoneOn = false
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val bluetoothDevice = audioManager.availableCommunicationDevices.firstOrNull {
                it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO || it.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP
            }
            if (bluetoothDevice != null) {
                audioManager.setCommunicationDevice(bluetoothDevice)
            }
        } else {
            audioManager.startBluetoothSco()
            audioManager.isBluetoothScoOn = true
        }
    }

    suspend fun releaseVoiceRoute() = withContext(dispatcher) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            audioManager.clearCommunicationDevice()
        } else {
            audioManager.stopBluetoothSco()
            audioManager.isBluetoothScoOn = false
        }
        audioManager.mode = AudioManager.MODE_NORMAL
    }
}
