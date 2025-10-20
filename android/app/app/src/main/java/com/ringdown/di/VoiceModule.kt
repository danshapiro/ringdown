package com.ringdown.di

import com.ringdown.BuildConfig
import com.ringdown.DebugFeatureFlags
import com.ringdown.data.voice.AudioRouteController
import com.ringdown.data.voice.FakeVoiceTransport
import com.ringdown.data.voice.VoiceTransport
import com.ringdown.data.voice.WebRtcVoiceTransport
import com.ringdown.domain.usecase.VoiceSessionCommandDispatcher
import com.ringdown.domain.usecase.VoiceSessionCommands
import com.ringdown.domain.usecase.VoiceSessionController
import com.ringdown.domain.usecase.VoiceSessionManager
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineDispatcher

@Module
@InstallIn(SingletonComponent::class)
abstract class VoiceTransportModule {

    @Binds
    @Singleton
    abstract fun bindVoiceTransport(
        impl: SelectedVoiceTransport
    ): VoiceTransport

    @Binds
    @Singleton
    abstract fun bindVoiceSessionController(
        manager: VoiceSessionManager
    ): VoiceSessionController

    @Binds
    @Singleton
    abstract fun bindVoiceSessionCommands(
        dispatcher: VoiceSessionCommandDispatcher
    ): VoiceSessionCommands
}

@Singleton
class SelectedVoiceTransport @Inject constructor(
    private val fake: FakeVoiceTransport,
    private val real: WebRtcVoiceTransport
) : VoiceTransport {

    private val delegate: VoiceTransport
        get() = if (DebugFeatureFlags.shouldUseVoiceTransportStub(BuildConfig.USE_FAKE_VOICE_TRANSPORT)) {
            fake
        } else {
            real
        }

    override suspend fun connect(parameters: VoiceTransport.ConnectParameters) {
        delegate.connect(parameters)
    }

    override suspend fun sendAudioFrame(frame: VoiceTransport.AudioFrame) {
        delegate.sendAudioFrame(frame)
    }

    override fun receiveAudioFrames() = delegate.receiveAudioFrames()

    override suspend fun teardown() {
        delegate.teardown()
    }
}

@Module
@InstallIn(SingletonComponent::class)
object VoiceSupportModule {

    @Provides
    @Singleton
    fun provideAudioRouteController(
        @ApplicationContext context: android.content.Context,
        @IoDispatcher dispatcher: CoroutineDispatcher
    ): AudioRouteController = AudioRouteController(context, dispatcher)
}
