package com.ringdown.mobile
import android.os.Build
import android.os.SystemClock
import android.util.Log
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.lifecycle.ViewModelProvider
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.data.store.DeviceIdStore
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.testing.RuntimePermissionRule
import com.ringdown.mobile.ui.MainUiState
import com.ringdown.mobile.ui.MainViewModel
import com.ringdown.mobile.voice.VoiceConnectionState
import dagger.hilt.android.testing.BindValue
import dagger.hilt.android.testing.HiltAndroidRule
import dagger.hilt.android.testing.HiltAndroidTest
import com.ringdown.mobile.testing.TEST_LIVE_DEVICE_ID_PROPERTY
import com.ringdown.mobile.testing.TEST_REGISTRATION_MODE_PROPERTY
import com.ringdown.mobile.testing.TEST_TEXT_SESSION_MODE_PROPERTY
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TestRule
import org.junit.runner.RunWith

@HiltAndroidTest
@RunWith(AndroidJUnit4::class)
class LiveServiceReconnectAndroidTest {

    @get:Rule(order = 0)
    val hiltRule = HiltAndroidRule(this)

    @get:Rule(order = 1)
    val microphoneRule: TestRule = RuntimePermissionRule.microphoneGranted()

    @get:Rule(order = 2)
    val bluetoothRule: TestRule =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            RuntimePermissionRule.bluetoothConnectGranted()
        } else {
            TestRule { base, _ -> base }
        }

    @field:BindValue
    lateinit var deviceIdStoreOverride: DeviceIdStore

    private lateinit var resolvedDeviceId: String
    private lateinit var testDataStoreScope: CoroutineScope
    private lateinit var testDataStore: DataStore<Preferences>
    private var previousRegistrationMode: String? = null
    private var previousTextSessionMode: String? = null
    private var previousLiveDeviceId: String? = null

    @Before
    fun setUp() {
        resolvedDeviceId = resolveDeviceId()
        previousRegistrationMode = setSystemProperty(TEST_REGISTRATION_MODE_PROPERTY, "live")
        previousTextSessionMode = setSystemProperty(TEST_TEXT_SESSION_MODE_PROPERTY, "live")
        previousLiveDeviceId = setSystemProperty(TEST_LIVE_DEVICE_ID_PROPERTY, resolvedDeviceId)
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        testDataStoreScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val storeFile = context.cacheDir.resolve("live_device_datastore.preferences_pb")
        testDataStore = PreferenceDataStoreFactory.create(
            scope = testDataStoreScope,
        ) {
            storeFile
        }
        val deviceKey = stringPreferencesKey("device_id")
        val agentKey = stringPreferencesKey("last_agent")
        val authKey = stringPreferencesKey("auth_token")
        val resumeKey = stringPreferencesKey("resume_token")
        runBlocking {
            testDataStore.edit { prefs ->
                prefs[deviceKey] = resolvedDeviceId
                prefs.remove(agentKey)
                prefs.remove(authKey)
                prefs.remove(resumeKey)
            }
        }
        logInfo(
            event = "live_test.datastore_configured",
            fields = mapOf(
                "path" to storeFile.absolutePath,
                "deviceId" to resolvedDeviceId,
            ),
        )
        deviceIdStoreOverride = DeviceIdStore(testDataStore)
        hiltRule.inject()
        logInfo(
            event = "live_test.device_configured",
            fields = mapOf("deviceId" to resolvedDeviceId),
        )
    }

    @After
    fun tearDown() {
        testDataStoreScope.cancel()
        restoreSystemProperty(TEST_REGISTRATION_MODE_PROPERTY, previousRegistrationMode)
        restoreSystemProperty(TEST_TEXT_SESSION_MODE_PROPERTY, previousTextSessionMode)
        restoreSystemProperty(TEST_LIVE_DEVICE_ID_PROPERTY, previousLiveDeviceId)
    }

    @Test
    fun connectsReconnectsAndHangsUpTwice() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            var attempts = 0
            var registration: MainUiState
            while (true) {
                registration = scenario.awaitRegisteredState(timeoutMillis = 60_000L)
                if (registration.registrationStatus is RegistrationStatus.Approved) {
                    break
                }
                attempts += 1
                if (attempts >= MAX_REGISTRATION_ATTEMPTS) {
                    break
                }
                logInfo(
                    event = "live_test.registration_retry",
                    fields = mapOf(
                        "attempt" to attempts,
                        "status" to (registration.registrationStatus?.javaClass?.simpleName ?: "null"),
                        "error" to registration.errorMessage,
                    ),
                )
                scenario.withMainViewModel { it.onCheckAgainClicked() }
                SystemClock.sleep(2_000L)
            }

            assertThat(registration.deviceId).isEqualTo(resolvedDeviceId)
            assertThat(registration.registrationStatus)
                .isInstanceOf(RegistrationStatus.Approved::class.java)
            assertThat(registration.errorMessage).isNull()
            val approved = registration.registrationStatus as RegistrationStatus.Approved

            logInfo(
                event = "live_test.registered",
                fields = mapOf(
                    "deviceId" to registration.deviceId,
                    "agent" to approved.agentName,
                    "backend" to BuildConfig.STAGING_BACKEND_BASE_URL,
                ),
            )

            scenario.withMainViewModel { viewModel ->
                viewModel.onPermissionResult(true)
                viewModel.startVoiceSession()
            }

            val firstConnected = scenario.awaitConnectedState(label = "first")

            scenario.withMainViewModel { it.stopVoiceSession() }
            scenario.awaitVoiceState(timeoutMillis = 30_000L) { state -> state is VoiceConnectionState.Idle }
            assertNoError(scenario)

            logInfo(
                event = "live_test.first_disconnect",
                fields = mapOf(
                    "transcripts" to firstConnected.transcripts.size,
                ),
            )

            SystemClock.sleep(5_000L)
            logInfo(
                event = "live_test.second_prestart",
                fields = emptyMap(),
            )

            scenario.withMainViewModel { viewModel ->
                val state = viewModel.state.value
                logInfo(
                    event = "live_test.second_start_state",
                    fields = mapOf(
                        "microphoneGranted" to state.microphonePermissionGranted,
                        "pendingAutoConnect" to state.pendingAutoConnect,
                        "voiceState" to state.voiceState::class.java.simpleName,
                        "registrationStatus" to (state.registrationStatus?.javaClass?.simpleName ?: "null"),
                    ),
                )
                viewModel.onPermissionResult(true)
                viewModel.startVoiceSession()
            }
            val secondConnected = scenario.awaitConnectedState(label = "second", timeoutMillis = 120_000L)

            scenario.withMainViewModel { it.stopVoiceSession() }
            scenario.awaitVoiceState(timeoutMillis = 30_000L) { state -> state is VoiceConnectionState.Idle }
            assertNoError(scenario)

            logInfo(
                event = "live_test.second_disconnect",
                fields = mapOf(
                    "transcripts" to secondConnected.transcripts.size,
                ),
            )
        }
    }

    private fun resolveDeviceId(): String {
        return System.getenv("LIVE_TEST_MOBILE_DEVICE_ID").orEmpty().ifBlank { DEFAULT_DEVICE_ID }
    }

    private fun ActivityScenario<MainActivity>.awaitConnectedState(
        label: String,
        timeoutMillis: Long = 60_000L,
    ): VoiceConnectionState.Connected {
        val state = awaitVoiceState(timeoutMillis = timeoutMillis) { voice ->
            voice is VoiceConnectionState.Connected || voice is VoiceConnectionState.Failed
        }
        if (state is VoiceConnectionState.Failed) {
            logError(
                event = "live_test.connection_failed",
                fields = mapOf(
                    "label" to label,
                    "reason" to state.reason,
                ),
            )
            error("Voice connection failed during $label attempt: ${state.reason}")
        }
        val connected = state as VoiceConnectionState.Connected
        logInfo(
            event = "live_test.connection_established",
            fields = mapOf(
                "label" to label,
                "transcripts" to connected.transcripts.size,
            ),
        )
        return connected
    }

    private fun assertNoError(scenario: ActivityScenario<MainActivity>) {
        var errorMessage: String? = null
        try {
            scenario.onActivity { activity ->
                val viewModel = ViewModelProvider(activity)[MainViewModel::class.java]
                errorMessage = viewModel.state.value.errorMessage
            }
        } catch (state: IllegalStateException) {
            logInfo(
                event = "live_test.ui_error_observed",
                fields = mapOf("message" to "scenario_closed", "reason" to state.message),
            )
            return
        }
        if (!errorMessage.isNullOrEmpty()) {
            logError(
                event = "live_test.ui_error_observed",
                fields = mapOf("message" to errorMessage),
            )
            error("MainViewModel exposed error: $errorMessage")
        }
    }

    private fun logInfo(event: String, fields: Map<String, Any?> = emptyMap()) {
        Log.i(TAG, buildLogPayload("INFO", event, fields))
    }

    private fun logError(event: String, fields: Map<String, Any?> = emptyMap()) {
        Log.e(TAG, buildLogPayload("ERROR", event, fields))
    }

    private fun buildLogPayload(level: String, event: String, fields: Map<String, Any?>): String {
        val builder = StringBuilder()
        builder.append("{\"severity\":\"").append(level).append("\",\"event\":\"").append(event).append("\"")
        for ((key, value) in fields) {
            builder.append(",\"").append(key).append("\":\"").append(value?.toString()?.replace("\"", "'") ?: "null").append("\"")
        }
        builder.append("}")
        return builder.toString()
    }

    companion object {
        private const val TAG = "LiveServiceReconnect"
        private const val DEFAULT_DEVICE_ID = "instrumentation-device"
        private const val MAX_REGISTRATION_ATTEMPTS = 3
    }

    private fun setSystemProperty(key: String, value: String): String? {
        val previous = System.getProperty(key)
        System.setProperty(key, value)
        return previous
    }

    private fun restoreSystemProperty(key: String, previous: String?) {
        if (previous == null) {
            System.clearProperty(key)
        } else {
            System.setProperty(key, previous)
        }
    }
}
