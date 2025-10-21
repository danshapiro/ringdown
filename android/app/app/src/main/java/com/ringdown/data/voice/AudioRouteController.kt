package com.ringdown.data.voice

import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.withContext

@Singleton
class AudioRouteController @Inject constructor(
    @ApplicationContext private val context: Context,
    private val dispatcher: CoroutineDispatcher
) {

    private val audioManager: AudioManager =
        context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val bluetoothTypes = setOf(
        AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
        AudioDeviceInfo.TYPE_BLUETOOTH_A2DP
    )
    private var speakerFallbackApplied = false

    suspend fun acquireVoiceRoute() = withContext(dispatcher) {
        audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        audioManager.isSpeakerphoneOn = false
        speakerFallbackApplied = false
        if (!routeToBluetooth()) {
            applySpeakerFallback()
        }
    }

    suspend fun releaseVoiceRoute() = withContext(dispatcher) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            audioManager.clearCommunicationDevice()
        } else {
            audioManager.stopBluetoothSco()
            audioManager.isBluetoothScoOn = false
        }
        if (speakerFallbackApplied) {
            audioManager.isSpeakerphoneOn = false
            speakerFallbackApplied = false
        }
        audioManager.mode = AudioManager.MODE_NORMAL
    }

    private fun routeToBluetooth(): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val bluetoothDevice = audioManager.availableCommunicationDevices.firstOrNull {
                it.type in bluetoothTypes
            }
            bluetoothDevice != null && audioManager.setCommunicationDevice(bluetoothDevice)
        } else {
            if (!hasConnectedBluetoothDevice()) {
                return false
            }
            audioManager.startBluetoothSco()
            audioManager.isBluetoothScoOn = true
            // Some devices require an additional check because SCO may fail silently.
            audioManager.isBluetoothScoOn
        }
    }

    private fun applySpeakerFallback() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val speakerDevice = audioManager.availableCommunicationDevices.firstOrNull {
                it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
            }
            speakerDevice?.let { audioManager.setCommunicationDevice(it) }
        } else {
            audioManager.stopBluetoothSco()
            audioManager.isBluetoothScoOn = false
        }
        audioManager.isSpeakerphoneOn = true
        speakerFallbackApplied = true
    }

    private fun hasConnectedBluetoothDevice(): Boolean {
        val bluetoothManager =
            context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
        val adapter = bluetoothManager?.adapter ?: BluetoothAdapter.getDefaultAdapter() ?: return false
        if (!adapter.isEnabled) {
            return false
        }
        return try {
            val headsetConnected =
                adapter.getProfileConnectionState(BluetoothProfile.HEADSET) == BluetoothProfile.STATE_CONNECTED
            val a2dpConnected =
                adapter.getProfileConnectionState(BluetoothProfile.A2DP) == BluetoothProfile.STATE_CONNECTED
            headsetConnected || a2dpConnected
        } catch (security: SecurityException) {
            false
        }
    }
}
