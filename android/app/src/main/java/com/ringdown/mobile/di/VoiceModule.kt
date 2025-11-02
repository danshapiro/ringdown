package com.ringdown.mobile.di

import com.ringdown.mobile.voice.ControlHarness
import com.ringdown.mobile.voice.DefaultControlHarness
import com.ringdown.mobile.voice.DefaultVoiceCallClientFactory
import com.ringdown.mobile.voice.VoiceCallClientFactory
import com.ringdown.mobile.voice.VoiceSessionController
import com.ringdown.mobile.voice.VoiceSessionGateway
import com.ringdown.mobile.voice.asr.AudioInputSource
import com.ringdown.mobile.voice.asr.LocalAsrEngine
import com.ringdown.mobile.voice.asr.MicrophoneAudioInputSource
import com.ringdown.mobile.voice.asr.SherpaOnnxAsrEngine
import com.ringdown.mobile.data.VoiceSessionDataSource
import com.ringdown.mobile.data.VoiceSessionRepository
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
abstract class VoiceModule {

    @Binds
    @Singleton
    abstract fun bindVoiceSessionGateway(
        controller: VoiceSessionController,
    ): VoiceSessionGateway

    @Binds
    @Singleton
    abstract fun bindVoiceCallClientFactory(
        factory: DefaultVoiceCallClientFactory,
    ): VoiceCallClientFactory

    @Binds
    @Singleton
    abstract fun bindControlHarness(
        harness: DefaultControlHarness,
    ): ControlHarness

    @Binds
    @Singleton
    abstract fun bindVoiceSessionDataSource(
        repository: VoiceSessionRepository,
    ): VoiceSessionDataSource

    @Binds
    @Singleton
    abstract fun bindLocalAsrEngine(
        engine: SherpaOnnxAsrEngine,
    ): LocalAsrEngine

    @Binds
    @Singleton
    abstract fun bindAudioInputSource(
        source: MicrophoneAudioInputSource,
    ): AudioInputSource
}
