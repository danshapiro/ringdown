package com.ringdown.mobile.di

import com.ringdown.mobile.voice.InstantProvider
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import java.time.Duration
import java.time.Instant
import javax.inject.Named
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object VoiceConfigModule {

    @Provides
    @Singleton
    @Named("voiceCallMinRefreshLead")
    fun provideMinRefreshLead(): Duration = Duration.ofSeconds(30)

    @Provides
    @Singleton
    fun provideInstantProvider(): InstantProvider = InstantProvider { Instant.now() }
}
