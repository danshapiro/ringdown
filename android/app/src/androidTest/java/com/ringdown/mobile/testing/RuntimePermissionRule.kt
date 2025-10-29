package com.ringdown.mobile.testing

import android.Manifest
import android.app.UiAutomation
import android.content.Context
import android.content.pm.PackageManager
import android.os.SystemClock
import android.util.Log
import androidx.core.content.ContextCompat
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.rules.TestRule
import org.junit.runner.Description
import org.junit.runners.model.Statement

/**
 * Ensures a runtime permission is forced into a predictable state before a test runs
 * and then restored to the device's original state afterward.
 */
class RuntimePermissionRule private constructor(
    private val permission: String,
    private val desiredState: DesiredState,
    private val restoreOriginalState: Boolean,
    private val timeoutMillis: Long = 3_000L,
    private val pollIntervalMillis: Long = 100L
) : TestRule {

    override fun apply(base: Statement, description: Description): Statement {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val targetContext = instrumentation.targetContext
        val packageName = targetContext.packageName

        return object : Statement() {
            override fun evaluate() {
                val wasGrantedBefore = isGranted(targetContext)
                logInfo(
                    event = "permission_rule_start",
                    details = mapOf(
                        "permission" to permission,
                        "wasGrantedBefore" to wasGrantedBefore,
                        "test" to description.displayName
                    )
                )

                try {
                    configureInitialState(instrumentation.uiAutomation, packageName, targetContext)
                    base.evaluate()
                } finally {
                    if (restoreOriginalState) {
                        restoreStateIfNeeded(
                            instrumentation.uiAutomation,
                            packageName,
                            targetContext,
                            wasGrantedBefore
                        )
                    }
                    logInfo(
                        event = "permission_rule_complete",
                        details = mapOf(
                            "permission" to permission,
                            "restoredGrant" to if (restoreOriginalState) wasGrantedBefore else "skipped",
                            "finalGranted" to isGranted(targetContext)
                        )
                    )
                }
            }
        }
    }

    private fun configureInitialState(
        uiAutomation: UiAutomation,
        packageName: String,
        context: Context
    ) {
        when (desiredState) {
            DesiredState.REVOKED -> {
                attemptRevoke(uiAutomation, packageName)
                waitForState(context, expectedGranted = false)
            }
            DesiredState.GRANTED -> {
                attemptGrant(uiAutomation, packageName)
                waitForState(context, expectedGranted = true)
            }
        }
    }

    private fun restoreStateIfNeeded(
        uiAutomation: UiAutomation,
        packageName: String,
        context: Context,
        originalGrant: Boolean
    ) {
        val current = isGranted(context)
        if (current == originalGrant) {
            return
        }
        if (originalGrant) {
            attemptGrant(uiAutomation, packageName)
            waitForState(context, expectedGranted = true)
        } else {
            attemptRevoke(uiAutomation, packageName)
            waitForState(context, expectedGranted = false)
        }
    }

    private fun attemptRevoke(uiAutomation: UiAutomation, packageName: String) {
        try {
            uiAutomation.revokeRuntimePermission(packageName, permission)
            logInfo(
                event = "permission_revoked",
                details = mapOf("permission" to permission)
            )
        } catch (security: SecurityException) {
            logWarn(
                event = "permission_revoke_failed",
                details = mapOf(
                    "permission" to permission,
                    "reason" to security.javaClass.simpleName
                )
            )
        } catch (illegal: IllegalArgumentException) {
            logWarn(
                event = "permission_revoke_failed",
                details = mapOf(
                    "permission" to permission,
                    "reason" to illegal.javaClass.simpleName
                )
            )
        }
    }

    private fun attemptGrant(uiAutomation: UiAutomation, packageName: String) {
        try {
            uiAutomation.grantRuntimePermission(packageName, permission)
            logInfo(
                event = "permission_granted",
                details = mapOf("permission" to permission)
            )
        } catch (security: SecurityException) {
            logWarn(
                event = "permission_grant_failed",
                details = mapOf(
                    "permission" to permission,
                    "reason" to security.javaClass.simpleName
                )
            )
        } catch (illegal: IllegalArgumentException) {
            logWarn(
                event = "permission_grant_failed",
                details = mapOf(
                    "permission" to permission,
                    "reason" to illegal.javaClass.simpleName
                )
            )
        }
    }

    private fun waitForState(context: Context, expectedGranted: Boolean) {
        val deadline = SystemClock.elapsedRealtime() + timeoutMillis
        while (SystemClock.elapsedRealtime() < deadline) {
            if (isGranted(context) == expectedGranted) {
                logInfo(
                    event = "permission_state_confirmed",
                    details = mapOf(
                        "permission" to permission,
                        "granted" to expectedGranted
                    )
                )
                return
            }
            SystemClock.sleep(pollIntervalMillis)
        }

        logWarn(
            event = "permission_state_timeout",
            details = mapOf(
                "permission" to permission,
                "expectedGranted" to expectedGranted,
                "actualGranted" to isGranted(context)
            )
        )
    }

    private fun isGranted(context: Context): Boolean {
        return ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED
    }

    private fun logInfo(event: String, details: Map<String, Any?> = emptyMap()) {
        Log.i(TAG, buildPayload("INFO", event, details))
    }

    private fun logWarn(event: String, details: Map<String, Any?> = emptyMap()) {
        Log.w(TAG, buildPayload("WARN", event, details))
    }

    private fun buildPayload(severity: String, event: String, details: Map<String, Any?>): String {
        val builder = StringBuilder()
        builder.append("{\"severity\":\"").append(severity).append("\",\"event\":\"").append(event).append("\"")
        for ((key, value) in details) {
            builder.append(",\"").append(key).append("\":\"").append(value.toSafeString()).append("\"")
        }
        builder.append("}")
        return builder.toString()
    }

    private fun Any?.toSafeString(): String {
        return this?.toString()?.replace("\"", "'") ?: "null"
    }

    companion object {
        private const val TAG = "RuntimePermissionRule"

        fun microphone(): RuntimePermissionRule {
            return RuntimePermissionRule(
                Manifest.permission.RECORD_AUDIO,
                DesiredState.REVOKED,
                restoreOriginalState = true
            )
        }

        fun microphoneGranted(restoreOriginalState: Boolean = true): RuntimePermissionRule {
            return RuntimePermissionRule(
                Manifest.permission.RECORD_AUDIO,
                DesiredState.GRANTED,
                restoreOriginalState = restoreOriginalState
            )
        }
    }
}

private enum class DesiredState {
    REVOKED,
    GRANTED,
}
