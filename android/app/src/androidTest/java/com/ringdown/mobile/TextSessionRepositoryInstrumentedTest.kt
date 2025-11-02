package com.ringdown.mobile

import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.BuildConfig
import com.ringdown.mobile.data.TextSessionGateway
import com.ringdown.mobile.data.store.DeviceIdStore
import dagger.hilt.android.testing.HiltAndroidRule
import dagger.hilt.android.testing.HiltAndroidTest
import java.util.Locale
import javax.inject.Inject
import kotlinx.coroutines.runBlocking
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@HiltAndroidTest
@RunWith(AndroidJUnit4::class)
class TextSessionRepositoryInstrumentedTest {

    @get:Rule
    val hiltRule = HiltAndroidRule(this)

    @Inject lateinit var gateway: TextSessionGateway
    @Inject lateinit var deviceIdStore: DeviceIdStore

    @Before
    fun setUp() {
        hiltRule.inject()
        val backendUrl = BuildConfig.STAGING_BACKEND_BASE_URL
        assumeTrue(
            "backendUrl must be configured (see README handset smoke instructions)",
            backendUrl.isNotBlank(),
        )
    }

    @Test
    fun startSession_roundTripsTokens() = runBlocking {
        deviceIdStore.updateAuthToken(null)
        deviceIdStore.updateResumeToken(null)

        val bootstrap = gateway.startTextSession(agent = null)

        assertThat(bootstrap.sessionId).isNotEmpty()
        assertThat(bootstrap.sessionToken).isNotEmpty()
        assertThat(bootstrap.websocketPath.lowercase(Locale.US)).contains("/v1/mobile/text")

        val persistedAuthToken = deviceIdStore.currentAuthToken()
        val persistedResumeToken = deviceIdStore.currentResumeToken()

        assertThat(persistedAuthToken).isNotNull()
        assertThat(persistedAuthToken).isNotEmpty()
        assertThat(persistedResumeToken).isEqualTo(bootstrap.resumeToken)

        Log.i(
            "TextSessionRepositoryTest",
            "Session id=${bootstrap.sessionId} token=${bootstrap.sessionToken.take(8)}…",
        )
    }
}
