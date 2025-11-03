package com.ringdown.mobile.di

import com.ringdown.mobile.voice.asr.AudioInputSource
import com.ringdown.mobile.voice.asr.LocalAsrEngine
import com.ringdown.mobile.voice.asr.MicrophoneAudioInputSource
import com.ringdown.mobile.voice.asr.SherpaOnnxAsrEngine
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
    abstract fun bindLocalAsrEngine(
        engine: SherpaOnnxAsrEngine,
    ): LocalAsrEngine

    @Binds
    @Singleton
    abstract fun bindAudioInputSource(
        source: MicrophoneAudioInputSource,
    ): AudioInputSource
}
