package com.ringdown.registration

import androidx.compose.ui.test.assertExists
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.waitUntilAtLeastOneExists
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.ringdown.DebugFeatureFlags
import com.ringdown.MainActivity
import dagger.hilt.android.testing.HiltAndroidRule
import dagger.hilt.android.testing.HiltAndroidTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@HiltAndroidTest
@RunWith(AndroidJUnit4::class)
class RegistrationSuite {

    private val mockWebServer = MockWebServer()

    @get:Rule(order = 0)
    val serverRule = MockServerRule(mockWebServer)

    @get:Rule(order = 1)
    val hiltRule = HiltAndroidRule(this)

    @get:Rule(order = 2)
    val composeRule = createAndroidComposeRule<MainActivity>()

    @Before
    fun setUp() {
        hiltRule.inject()
    }

    @Test
    fun pendingApprovesAndReturnsToIdle() {
        mockWebServer.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """{"status":"PENDING","message":"Awaiting administrator approval","pollAfterSeconds":1}"""
                )
                .setHeader("Content-Type", "application/json")
        )
        mockWebServer.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"status":"APPROVED","message":"Device approved"}""")
                .setHeader("Content-Type", "application/json")
        )

        composeRule.waitUntilAtLeastOneExists(hasText("Approval Required"), 5_000)
        composeRule.onNodeWithText("Check again").assertExists()

        composeRule.waitUntilAtLeastOneExists(hasText("Reconnect"), 10_000)
        composeRule.onNodeWithText("Reconnect").assertExists()

        assertEquals(2, mockWebServer.requestCount)
    }

    private class MockServerRule(
        private val server: MockWebServer
    ) : org.junit.rules.TestRule {
        override fun apply(base: org.junit.runners.model.Statement, description: org.junit.runner.Description): org.junit.runners.model.Statement {
            return object : org.junit.runners.model.Statement() {
                override fun evaluate() {
                    DebugFeatureFlags.overrideRegistrationStub(false)
                    server.start(8899)
                    try {
                        base.evaluate()
                    } finally {
                        server.shutdown()
                        DebugFeatureFlags.overrideRegistrationStub(null)
                    }
                }
            }
        }
    }
}
