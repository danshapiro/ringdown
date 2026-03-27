package com.ringdown.mobile.di

import android.util.Log
import androidx.test.platform.app.InstrumentationRegistry
import com.ringdown.mobile.chat.ChatSessionController
import com.ringdown.mobile.chat.ChatSessionGateway
import com.ringdown.mobile.data.TextSessionRepository
import com.ringdown.mobile.data.TextSessionStarter
import com.ringdown.mobile.domain.TextSessionBootstrap
import com.ringdown.mobile.testing.TEST_TEXT_SESSION_MODE_ARGUMENT
import com.ringdown.mobile.testing.TEST_TEXT_SESSION_MODE_PROPERTY
import dagger.Module
import dagger.Provides
import dagger.hilt.components.SingletonComponent
import dagger.hilt.testing.TestInstallIn
import java.time.Instant
import java.util.Locale
import java.util.UUID
import javax.inject.Singleton

@Module
@TestInstallIn(
    components = [SingletonComponent::class],
    replaces = [TextSessionModule::class],
)
object TestTextSessionModule {

    @Provides
    @Singleton
    fun provideTextSessionStarter(
        repository: TextSessionRepository,
    ): TextSessionStarter {
        return if (shouldUseLiveTextSession()) {
            repository
        } else {
            FakeTextSessionStarter()
        }
    }

    @Provides
    @Singleton
    fun provideChatSessionGateway(
        controller: ChatSessionController,
    ): ChatSessionGateway = controller

    private fun shouldUseLiveTextSession(): Boolean {
        val argument = InstrumentationRegistry.getArguments()
            .getString(TEST_TEXT_SESSION_MODE_ARGUMENT)
            ?.lowercase(Locale.US)
        val property = System.getProperty(TEST_TEXT_SESSION_MODE_PROPERTY)?.lowercase(Locale.US)
        val env = System.getenv(ENV_TEXT_SESSION_MODE)?.lowercase(Locale.US)
        val live = listOf(argument, property, env).any { candidate ->
            candidate == LIVE_VALUE || candidate == PRODUCTION_VALUE
        }
        Log.i(
            TAG,
            "{\"severity\":\"INFO\",\"event\":\"text_session_mode\",\"argument\":\"$argument\",\"property\":\"$property\",\"env\":\"$env\",\"live\":$live}",
        )
        return live
    }

    private class FakeTextSessionStarter : TextSessionStarter {
        override suspend fun startTextSession(agent: String?): TextSessionBootstrap {
            val sessionId = "fake-session-${UUID.randomUUID()}"
            val resumeToken = "fake-resume-${UUID.randomUUID()}"
            val now = Instant.now()
            return TextSessionBootstrap(
                sessionId = sessionId,
                sessionToken = UUID.randomUUID().toString(),
                resumeToken = resumeToken,
                websocketPath = "fake://session/$sessionId",
                agent = agent ?: "Instrumentation Agent",
                expiresAtIso = now.plusSeconds(120).toString(),
                heartbeatIntervalSeconds = 15,
                heartbeatTimeoutSeconds = 45,
                tlsPins = emptyList(),
            )
        }
    }

    private const val TAG = "TestTextSessionModule"
    private const val ENV_TEXT_SESSION_MODE = "RINGDOWN_TEST_TEXT_SESSION_MODE"
    private const val LIVE_VALUE = "live"
    private const val PRODUCTION_VALUE = "production"
}
