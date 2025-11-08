package com.ringdown.mobile.voice

import android.content.Context
import android.media.MediaPlayer
import android.os.Handler
import android.os.Looper
import com.ringdown.mobile.R
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ReconnectTonePlayer @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    private val mainHandler = Handler(Looper.getMainLooper())
    private var mediaPlayer: MediaPlayer? = null
    private var shouldPlay: Boolean = false

    fun startLoop() {
        if (shouldPlay) return
        shouldPlay = true
        mainHandler.post { ensurePlayerStarted() }
    }

    fun stop() {
        shouldPlay = false
        mainHandler.post {
            mediaPlayer?.stop()
            mediaPlayer?.release()
            mediaPlayer = null
        }
    }

    private fun ensurePlayerStarted() {
        if (!shouldPlay) {
            return
        }
        if (mediaPlayer?.isPlaying == true) {
            return
        }
        mediaPlayer?.release()
        mediaPlayer = MediaPlayer.create(context, R.raw.reconnecting_tone)?.apply {
            isLooping = true
            setOnCompletionListener(null)
            start()
        }
        if (mediaPlayer == null) {
            shouldPlay = false
        }
    }
}
