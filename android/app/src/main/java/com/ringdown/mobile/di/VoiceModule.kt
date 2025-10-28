package com.ringdown.mobile.di

import com.ringdown.mobile.data.VoiceSessionDataSource
import com.ringdown.mobile.data.VoiceSessionRepository
import com.ringdown.mobile.voice.DefaultVoiceCallClientFactory
import com.ringdown.mobile.voice.VoiceSessionController
import com.ringdown.mobile.voice.VoiceSessionGateway
import com.ringdown.mobile.voice.VoiceCallClientFactory
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
    abstract fun bindVoiceSessionDataSource(
        repository: VoiceSessionRepository,
    ): VoiceSessionDataSource
}
