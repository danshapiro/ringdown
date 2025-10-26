package com.ringdown.mobile.di

import com.ringdown.mobile.voice.VoiceSessionController
import com.ringdown.mobile.voice.VoiceSessionGateway
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
}
