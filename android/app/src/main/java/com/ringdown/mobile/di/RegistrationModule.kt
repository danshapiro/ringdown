package com.ringdown.mobile.di

import com.ringdown.mobile.data.RegistrationGateway
import com.ringdown.mobile.data.RegistrationRepository
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
abstract class RegistrationModule {
    @Binds
    @Singleton
    abstract fun bindRegistrationGateway(
        repository: RegistrationRepository,
    ): RegistrationGateway
}
