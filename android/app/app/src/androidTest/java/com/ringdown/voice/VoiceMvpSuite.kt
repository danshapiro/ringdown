package com.ringdown.voice

import android.Manifest
import android.os.Build
import android.os.Build.VERSION_CODES
import android.os.SystemClock
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.ringdown.DebugFeatureFlags
import com.ringdown.MainActivity
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

class VoiceMvpSuite {

    private val mockWebServer = MockWebServer()
    private val backendOverride: String? by lazy {
        InstrumentationRegistry.getArguments().getString("backendUrl")
    }
    private val deviceIdOverride: String by lazy {
        InstrumentationRegistry.getArguments().getString("deviceIdOverride") ?: "instrumentation-device"
    }

    @get:Rule(order = 0)
    val serverRule = MockServerRule(mockWebServer)

    @get:Rule(order = 1)
    val composeRule = createAndroidComposeRule<MainActivity>()

    @Before
    fun setUp() {
        if (backendOverride == null) {
            enqueueApprovedResponse()
        }
    }

    @After
    fun tearDown() {
        DebugFeatureFlags.overrideRegistrationStub(null)
        DebugFeatureFlags.overrideVoiceTransportStub(null)
        DebugFeatureFlags.overrideBackendBaseUrl(null)
        DebugFeatureFlags.overrideDeviceId(null)
    }

    @Test
    fun connectAndHangUpSucceeds() {
        composeRule.waitForIdle()

        waitForVoiceSessionReady()

        composeRule.onNodeWithText("Hang Up").assertIsDisplayed()
        composeRule.onNodeWithText("Hang Up").performClick()

        composeRule.waitUntil(5_000) { hasNode("Reconnect") }
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

    inner class MockServerRule(
        private val server: MockWebServer
    ) : org.junit.rules.TestRule {

        private var startedLocally = false

        override fun apply(base: org.junit.runners.model.Statement, description: org.junit.runner.Description): org.junit.runners.model.Statement {
            return object : org.junit.runners.model.Statement() {
                override fun evaluate() {
                    val instrumentation = InstrumentationRegistry.getInstrumentation()
                    val packageName = instrumentation.targetContext.packageName
                    val permissions = mutableListOf(Manifest.permission.RECORD_AUDIO)
                    if (Build.VERSION.SDK_INT >= VERSION_CODES.S) {
                        permissions += Manifest.permission.BLUETOOTH_CONNECT
                    }
                    permissions.forEach { permission ->
                        try {
                            instrumentation.uiAutomation.grantRuntimePermission(packageName, permission)
                        } catch (_: Exception) {
                            // Ignore if permission is unavailable or already granted.
                        }
                    }
                    DebugFeatureFlags.overrideDeviceId(this@VoiceMvpSuite.deviceIdOverride)
                    val backendOverride = InstrumentationRegistry.getArguments().getString("backendUrl")
                    DebugFeatureFlags.overrideRegistrationStub(false)
                    if (backendOverride.isNullOrBlank()) {
                        DebugFeatureFlags.overrideVoiceTransportStub(true)
                    } else {
                        DebugFeatureFlags.overrideVoiceTransportStub(null)
                    }
                    val activeBackendUrl = if (backendOverride.isNullOrBlank()) {
                        ensureServerStarted()
                        server.url("/").toString()
                    } else {
                        backendOverride
                    }
                    DebugFeatureFlags.overrideBackendBaseUrl(activeBackendUrl)
                    try {
                        base.evaluate()
                    } finally {
                        if (backendOverride.isNullOrBlank() && startedLocally) {
                            server.shutdown()
                            startedLocally = false
                        }
                        DebugFeatureFlags.clearOverrides()
                    }
                }
            }
        }

        private fun ensureServerStarted() {
            if (startedLocally) {
                return
            }
            try {
                server.start()
                startedLocally = true
            } catch (error: IllegalStateException) {
                // Server already active for this process; reuse the existing instance.
                startedLocally = false
            }
        }
    }

    private fun hasNode(text: String): Boolean = runCatching {
        composeRule.onNodeWithText(text).assertExists()
    }.isSuccess

    private fun waitForVoiceSessionReady(timeoutMs: Long = 120_000) {
        val deadline = SystemClock.elapsedRealtime() + timeoutMs
        while (SystemClock.elapsedRealtime() < deadline) {
            when {
                hasNode("Hang Up") -> return
                hasNode("Reconnect") -> {
                    composeRule.onNodeWithText("Reconnect").performClick()
                }
                hasNode("Check again") -> {
                    composeRule.onNodeWithText("Check again").performClick()
                }
            }
            composeRule.waitForIdle()
            if (hasNode("Hang Up")) {
                return
            }
            SystemClock.sleep(500)
        }
        throw AssertionError("Timed out waiting for voice session to present Hang Up button.")
    }
}
