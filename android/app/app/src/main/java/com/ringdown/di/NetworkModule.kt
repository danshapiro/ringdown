package com.ringdown.di

import com.ringdown.BuildConfig
import com.ringdown.DebugFeatureFlags
import com.ringdown.data.registration.RegistrationApi
import com.ringdown.data.registration.RegistrationRepository
import com.ringdown.data.registration.RegistrationRepositoryImpl
import com.ringdown.data.registration.StubRegistrationInterceptor
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.moshi.MoshiConverterFactory

@Module
@InstallIn(SingletonComponent::class)
abstract class RepositoryModule {

    @Binds
    @Singleton
    abstract fun bindRegistrationRepository(
        impl: RegistrationRepositoryImpl
    ): RegistrationRepository
}

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {

    @Provides
    @Singleton
    fun provideMoshi(): Moshi = Moshi.Builder()
        .add(KotlinJsonAdapterFactory())
        .build()

    @Provides
    @StubApprovalThreshold
    fun provideStubApprovalThreshold(): Int = BuildConfig.STUB_APPROVAL_THRESHOLD

    @Provides
    @Singleton
    fun provideOkHttpClient(
        stubInterceptor: StubRegistrationInterceptor
    ): OkHttpClient {
        val logging = HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) {
                HttpLoggingInterceptor.Level.BODY
            } else {
                HttpLoggingInterceptor.Level.NONE
            }
        }

        return OkHttpClient.Builder().apply {
            if (DebugFeatureFlags.shouldUseRegistrationStub(BuildConfig.USE_STUB_REGISTRATION)) {
                addInterceptor(stubInterceptor)
            }
            addInterceptor(logging)
        }.build()
    }

    @Provides
    @Singleton
    fun provideRetrofit(
        okHttpClient: OkHttpClient,
        moshi: Moshi
    ): Retrofit = Retrofit.Builder()
        .baseUrl(
            DebugFeatureFlags.backendBaseUrlOrDefault(BuildConfig.BACKEND_BASE_URL)
        )
        .client(okHttpClient)
        .addConverterFactory(MoshiConverterFactory.create(moshi))
        .build()

    @Provides
    @Singleton
    fun provideRegistrationApi(retrofit: Retrofit): RegistrationApi =
        retrofit.create(RegistrationApi::class.java)
}
