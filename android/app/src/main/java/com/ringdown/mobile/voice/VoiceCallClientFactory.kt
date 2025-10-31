package com.ringdown.mobile.voice

import android.content.Context
import android.os.Handler
import android.os.Looper
import co.daily.CallClient
import co.daily.CallClientListener
import co.daily.model.CallJoinData
import co.daily.model.MeetingToken
import co.daily.model.RequestListener
import co.daily.model.RequestListenerWithData
import co.daily.model.RequestResult
import co.daily.model.RequestResultWithData
import co.daily.model.customtrack.CustomAudioSource
import co.daily.model.customtrack.CustomTrackName
import com.ringdown.mobile.domain.ManagedVoiceSession
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

interface VoiceCallClient {
    fun attachListener(listener: CallClientListener)
    fun detachListener(listener: CallClientListener)
    fun join(session: ManagedVoiceSession, onError: (String?) -> Unit)
    fun leave(onComplete: () -> Unit)
    fun release()
    fun addCustomAudioTrack(
        name: CustomTrackName,
        source: CustomAudioSource,
        onResult: (RequestResult?) -> Unit = {},
    )
    fun removeCustomAudioTrack(
        name: CustomTrackName,
        onResult: (RequestResult?) -> Unit = {},
    )
}

interface VoiceCallClientFactory {
    fun create(): VoiceCallClient
}

@Singleton
class DefaultVoiceCallClientFactory @Inject constructor(
    @ApplicationContext private val context: Context,
) : VoiceCallClientFactory {

    override fun create(): VoiceCallClient {
        val callClient = CallClient(
            context.applicationContext,
            /* lifecycle = */ null,
            Handler(Looper.getMainLooper()),
        )
        return RealVoiceCallClient(callClient)
    }
}

private class RealVoiceCallClient(
    private val delegate: CallClient,
) : VoiceCallClient {

    override fun attachListener(listener: CallClientListener) {
        delegate.addListener(listener)
    }

    override fun detachListener(listener: CallClientListener) {
        delegate.removeListener(listener)
    }

    override fun join(
        session: ManagedVoiceSession,
        onError: (String?) -> Unit,
    ) {
        delegate.join(
            session.roomUrl,
            MeetingToken(session.accessToken),
            co.daily.settings.ClientSettingsUpdate(),
            object : RequestListenerWithData<CallJoinData> {
                override fun onRequestResult(result: RequestResultWithData<CallJoinData>) {
                    if (result.isError) {
                        onError(result.error?.msg)
                    } else {
                        onError(null)
                    }
                }
            },
        )
    }

    override fun leave(onComplete: () -> Unit) {
        delegate.leave(
            object : RequestListener {
                override fun onRequestResult(result: RequestResult) {
                    onComplete()
                }
            },
        )
    }

    override fun release() {
        delegate.release()
    }

    override fun addCustomAudioTrack(
        name: CustomTrackName,
        source: CustomAudioSource,
        onResult: (RequestResult?) -> Unit,
    ) {
        delegate.addCustomAudioTrack(
            name,
            source,
            object : RequestListener {
                override fun onRequestResult(result: RequestResult) {
                    onResult(result)
                }
            },
        )
    }

    override fun removeCustomAudioTrack(
        name: CustomTrackName,
        onResult: (RequestResult?) -> Unit,
    ) {
        delegate.removeCustomAudioTrack(
            name,
            object : RequestListener {
                override fun onRequestResult(result: RequestResult) {
                    onResult(result)
                }
            },
        )
    }
}
