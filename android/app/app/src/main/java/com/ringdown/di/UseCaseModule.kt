package com.ringdown.di

import com.ringdown.domain.usecase.RefreshRegistrationStatusUseCase
import com.ringdown.domain.usecase.RegistrationStatusRefresher
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent

@Module
@InstallIn(SingletonComponent::class)
abstract class UseCaseModule {

    @Binds
    abstract fun bindRegistrationStatusRefresher(
        useCase: RefreshRegistrationStatusUseCase
    ): RegistrationStatusRefresher
}
