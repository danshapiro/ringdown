package com.ringdown.registration

import android.Manifest
import android.os.Build
import android.os.Build.VERSION_CODES
import android.os.SystemClock
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.printToLog
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.ringdown.DebugFeatureFlags
import com.ringdown.MainActivity
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
@OptIn(ExperimentalTestApi::class)
class RegistrationSuite {

    private val mockWebServer = MockWebServer()
    private val deviceIdOverride: String by lazy {
        InstrumentationRegistry.getArguments().getString("deviceIdOverride") ?: "instrumentation-device"
    }
    private val backendOverride: String? by lazy {
        InstrumentationRegistry.getArguments().getString("backendUrl")
    }

    @get:Rule(order = 0)
    val serverRule = MockServerRule(mockWebServer)

    @get:Rule(order = 1)
    val composeRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun deviceApprovalFlowCompletes() {
        composeRule.waitForIdle()
        if (backendOverride.isNullOrBlank()) {
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
        }

        composeRule.waitUntil(15_000) {
            runCatching { composeRule.onRoot().assertExists() }.isSuccess
        }
        composeRule.onRoot().printToLog("RegistrationSuite")

        when (val initialOutcome = awaitInitialState()) {
            RegistrationOutcome.Pending -> handlePendingPath()
            RegistrationOutcome.Voice -> ensureVoiceSessionAndReturnToIdle()
            RegistrationOutcome.Idle -> assertIdleScreen()
        }

        if (backendOverride.isNullOrBlank()) {
            assertEquals(2, mockWebServer.requestCount)
        }
    }

    private fun handlePendingPath() {
        composeRule.onNodeWithText("Approval Required").assertIsDisplayed()
        composeRule.onNodeWithText("Check again").assertIsDisplayed()
        when (awaitReadyStateAfterPending()) {
            ReadyOutcome.Idle -> assertIdleScreen()
            ReadyOutcome.Voice -> ensureVoiceSessionAndReturnToIdle()
        }
    }

    private fun awaitInitialState(): RegistrationOutcome {
        composeRule.waitUntil(30_000) {
            pendingVisible() || voiceVisible() || idleVisible()
        }
        return when {
            pendingVisible() -> RegistrationOutcome.Pending
            voiceVisible() -> RegistrationOutcome.Voice
            else -> RegistrationOutcome.Idle
        }
    }

    private fun awaitReadyStateAfterPending(): ReadyOutcome {
        composeRule.waitUntil(30_000) {
            voiceVisible() || idleVisible()
        }
        return if (voiceVisible()) ReadyOutcome.Voice else ReadyOutcome.Idle
    }

    private fun ensureVoiceSessionAndReturnToIdle() {
        waitForVoiceSessionReady()
        if (backendOverride.isNullOrBlank()) {
            verifyMockBackendVoiceFlow()
        } else {
            verifyRealBackendVoiceFlow()
        }
    }

    private fun verifyMockBackendVoiceFlow() {
        when {
            hasNodeWithText("Hang Up") -> {
                composeRule.onNodeWithText("Hang Up").assertIsDisplayed()
                composeRule.onNodeWithText("Hang Up").performClick()
            }
            hasNodeWithText("Cancel") -> {
                composeRule.onNodeWithText("Cancel").assertIsDisplayed()
                composeRule.onNodeWithText("Cancel").performClick()
            }
            else -> throw AssertionError("Expected voice controls while using MockWebServer backend.")
        }
        composeRule.waitUntil(20_000) { idleVisible() }
        composeRule.onNodeWithText("Reconnect").assertIsDisplayed()
    }

    private fun verifyRealBackendVoiceFlow() {
        when {
            hasNodeWithText("Hang Up") -> composeRule.onNodeWithText("Hang Up").assertIsDisplayed()
            hasNodeWithText("Cancel") -> composeRule.onNodeWithText("Cancel").assertIsDisplayed()
            else -> throw AssertionError("Expected live backend flow to surface voice controls.")
        }
    }

    private fun assertIdleScreen() {
        composeRule.waitUntil(10_000) { idleVisible() }
        composeRule.onNodeWithText("Reconnect").assertIsDisplayed()
    }

    private fun waitForVoiceSessionReady(timeoutMs: Long = 120_000) {
        val deadline = SystemClock.elapsedRealtime() + timeoutMs
        while (SystemClock.elapsedRealtime() < deadline) {
            when {
                voiceVisible() -> return
                idleVisible() -> composeRule.onNodeWithText("Reconnect").performClick()
                hasNodeWithText("Check again") -> composeRule.onNodeWithText("Check again").performClick()
            }
            composeRule.waitForIdle()
            SystemClock.sleep(500)
        }
        throw AssertionError("Timed out waiting for voice session controls to appear.")
    }

    private fun pendingVisible(): Boolean = hasNodeWithText("Approval Required")

    private fun voiceVisible(): Boolean = hasNodeWithText("Hang Up") || hasNodeWithText("Cancel")

    private fun idleVisible(): Boolean = hasNodeWithText("Reconnect")

    private fun hasNodeWithText(text: String): Boolean = try {
        composeRule.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty()
    } catch (_: Throwable) {
        false
    }

    private enum class RegistrationOutcome {
        Pending,
        Voice,
        Idle
    }

    private enum class ReadyOutcome {
        Voice,
        Idle
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
                            // Permission may already be granted or unavailable on this API level.
                        }
                    }
                    DebugFeatureFlags.overrideDeviceId(this@RegistrationSuite.deviceIdOverride)
                    DebugFeatureFlags.overrideRegistrationStub(false)
                    if (this@RegistrationSuite.backendOverride.isNullOrBlank()) {
                        DebugFeatureFlags.overrideVoiceTransportStub(true)
                    } else {
                        DebugFeatureFlags.overrideVoiceTransportStub(null)
                    }
                    val activeBackendUrl = if (this@RegistrationSuite.backendOverride.isNullOrBlank()) {
                        ensureServerStarted()
                        server.url("/").toString()
                    } else {
                        this@RegistrationSuite.backendOverride
                    }
                    DebugFeatureFlags.overrideBackendBaseUrl(activeBackendUrl)
                    try {
                        base.evaluate()
                    } finally {
                        if (this@RegistrationSuite.backendOverride.isNullOrBlank() && startedLocally) {
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
                startedLocally = false
            }
        }
    }
}
