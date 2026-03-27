package com.ringdown.mobile.di

import com.ringdown.mobile.chat.ChatSessionController
import com.ringdown.mobile.chat.ChatSessionGateway
import com.ringdown.mobile.data.TextSessionRepository
import com.ringdown.mobile.data.TextSessionStarter
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
    abstract fun bindTextSessionStarter(
        repository: TextSessionRepository,
    ): TextSessionStarter

    @Binds
    @Singleton
    abstract fun bindChatSessionGateway(
        controller: ChatSessionController,
    ): ChatSessionGateway
}
