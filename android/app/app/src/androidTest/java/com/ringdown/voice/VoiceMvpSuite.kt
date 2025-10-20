package com.ringdown.voice

import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.ringdown.DebugFeatureFlags
import com.ringdown.MainActivity
import dagger.hilt.android.testing.HiltAndroidRule
import dagger.hilt.android.testing.HiltAndroidTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@HiltAndroidTest
@RunWith(AndroidJUnit4::class)
@OptIn(ExperimentalTestApi::class)
class VoiceMvpSuite {

    private val mockWebServer = MockWebServer()
    private val backendOverride: String? by lazy {
        InstrumentationRegistry.getArguments().getString("backendUrl")
    }

    @get:Rule(order = 0)
    val serverRule = MockServerRule(mockWebServer)

    @get:Rule(order = 1)
    val hiltRule = HiltAndroidRule(this)

    @get:Rule(order = 2)
    val composeRule = createAndroidComposeRule<MainActivity>()

    @Before
    fun setUp() {
        hiltRule.inject()
        if (backendOverride == null) {
            enqueueApprovedResponse()
        }
    }

    @After
    fun tearDown() {
        DebugFeatureFlags.overrideRegistrationStub(null)
        DebugFeatureFlags.overrideVoiceTransportStub(null)
    }

    @Test
    fun connectAndHangUpSucceeds() {
        composeRule.waitUntil(5_000) {
            composeRule.onAllNodesWithText("Hang Up").fetchSemanticsNodes().isNotEmpty()
        }

        composeRule.onNodeWithText("Hang Up").assertIsDisplayed()
        composeRule.onNodeWithText("Hang Up").performClick()

        composeRule.waitUntil(5_000) {
            composeRule.onAllNodesWithText("Reconnect").fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithText("Reconnect").assertIsDisplayed()
    }

    private fun enqueueApprovedResponse() {
        repeat(3) {
            mockWebServer.enqueue(
                MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody("""{"status":"APPROVED","message":"Device approved"}""")
            )
        }
    }

    class MockServerRule(
        private val server: MockWebServer
    ) : org.junit.rules.TestRule {
        override fun apply(base: org.junit.runners.model.Statement, description: org.junit.runner.Description): org.junit.runners.model.Statement {
            return object : org.junit.runners.model.Statement() {
                override fun evaluate() {
                    DebugFeatureFlags.overrideRegistrationStub(false)
                    DebugFeatureFlags.overrideVoiceTransportStub(true)
                    val backendOverride = InstrumentationRegistry.getArguments().getString("backendUrl")
                    if (backendOverride == null) {
                        server.start(8899)
                    }
                    try {
                        base.evaluate()
                    } finally {
                        if (backendOverride == null) {
                            server.shutdown()
                        }
                        DebugFeatureFlags.overrideRegistrationStub(null)
                        DebugFeatureFlags.overrideVoiceTransportStub(null)
                    }
                }
            }
        }
    }
}
