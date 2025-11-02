package com.ringdown.mobile

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.TextSessionRepository
import com.ringdown.mobile.data.remote.TextSessionApi
import com.ringdown.mobile.data.store.DeviceIdStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import org.junit.Assume
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import retrofit2.Retrofit
import retrofit2.converter.moshi.MoshiConverterFactory
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import retrofit2.HttpException

@RunWith(AndroidJUnit4::class)
class TextSessionRepositoryInstrumentedTest {

    private lateinit var repository: TextSessionRepository
    private lateinit var deviceIdStore: DeviceIdStore
    private lateinit var baseUrl: String

    @Before
    fun setUp() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val arguments = instrumentation.arguments
        baseUrl = arguments.getString("textSessionBaseUrl", "")

        Assume.assumeTrue("textSessionBaseUrl not provided", baseUrl.isNotBlank())

        val context = instrumentation.targetContext
        val dataStore = DeviceIdStore.createDataStore(context)
        deviceIdStore = DeviceIdStore(dataStore)

        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
        }
        val client = OkHttpClient.Builder()
            .addInterceptor(logging)
            .build()

        val moshi = Moshi.Builder()
            .addLast(KotlinJsonAdapterFactory())
            .build()

        val retrofit = Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(MoshiConverterFactory.create(moshi))
            .build()

        val api = retrofit.create(TextSessionApi::class.java)
        repository = TextSessionRepository(api, deviceIdStore, Dispatchers.IO)
    }

    @Test
    fun startSession_roundTripsTokens() = runBlocking {
        deviceIdStore.updateAuthToken(null)
        deviceIdStore.updateResumeToken(null)

        val bootstrap = try {
            repository.startTextSession()
        } catch (error: HttpException) {
            Assume.assumeTrue(
                "Text session endpoint unavailable (${error.code()})",
                false,
            )
            return@runBlocking
        }

        assertThat(bootstrap.sessionId).isNotEmpty()
        assertThat(bootstrap.sessionToken).isNotEmpty()
        assertThat(bootstrap.websocketPath.lowercase()).contains("/v1/mobile/text")

        val persistedAuthToken = deviceIdStore.currentAuthToken()
        val persistedResumeToken = deviceIdStore.currentResumeToken()

        assertThat(persistedAuthToken).isNotNull()
        assertThat(persistedAuthToken).isNotEmpty()
        assertThat(persistedResumeToken).isEqualTo(bootstrap.resumeToken)
    }
}
