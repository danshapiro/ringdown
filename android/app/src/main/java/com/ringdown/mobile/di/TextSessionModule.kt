package com.ringdown.mobile.di

import com.ringdown.mobile.data.TextSessionGateway
import com.ringdown.mobile.data.TextSessionRepository
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
abstract class TextSessionModule {

    @Binds
    @Singleton
    abstract fun bindTextSessionGateway(
        repository: TextSessionRepository,
    ): TextSessionGateway
}
