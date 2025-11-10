package com.ringdown.mobile.voice

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.ArrayDeque
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

interface GreetingSpeechGateway {
    fun speak(text: String)
    fun stop()
}

@Singleton
class GreetingSpeechPlayer @Inject constructor(
    @ApplicationContext private val context: Context,
) : GreetingSpeechGateway {

    private val handler = Handler(Looper.getMainLooper())
    private var textToSpeech: TextToSpeech? = null
    private var initialised = false
    private val pending: ArrayDeque<String> = ArrayDeque()

    override fun speak(text: String) {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return
        handler.post {
            ensureEngine()
            if (!initialised) {
                pending += trimmed
                return@post
            }
            speakInternal(trimmed)
        }
    }

    override fun stop() {
        handler.post {
            pending.clear()
            textToSpeech?.stop()
        }
    }

    private fun ensureEngine() {
        if (textToSpeech != null) {
            return
        }
        textToSpeech = TextToSpeech(context) { status ->
            handler.post {
                initialised = status == TextToSpeech.SUCCESS
                if (initialised) {
                    textToSpeech?.language = Locale.US
                    while (pending.isNotEmpty()) {
                        speakInternal(pending.removeFirst())
                    }
                } else {
                    pending.clear()
                }
            }
        }
    }

    private fun speakInternal(text: String) {
        val engine = textToSpeech ?: return
        engine.speak(
            text,
            TextToSpeech.QUEUE_FLUSH,
            null,
            "greeting-${System.currentTimeMillis()}",
        )
    }
}
