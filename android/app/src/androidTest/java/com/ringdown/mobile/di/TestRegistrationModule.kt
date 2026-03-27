package com.ringdown.mobile.di

import android.util.Log
import androidx.test.platform.app.InstrumentationRegistry
import com.ringdown.mobile.data.DeviceDescriptor
import com.ringdown.mobile.data.RegistrationGateway
import com.ringdown.mobile.data.RegistrationRepository
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.testing.TEST_LIVE_DEVICE_ID_ARGUMENT
import com.ringdown.mobile.testing.TEST_LIVE_DEVICE_ID_PROPERTY
import com.ringdown.mobile.testing.TEST_REGISTRATION_MODE_ARGUMENT
import com.ringdown.mobile.testing.TEST_REGISTRATION_MODE_PROPERTY
import dagger.Module
import dagger.Provides
import dagger.hilt.components.SingletonComponent
import dagger.hilt.testing.TestInstallIn
import java.util.Locale
import java.util.UUID
import java.util.concurrent.atomic.AtomicReference
import javax.inject.Singleton

@Module
@TestInstallIn(
    components = [SingletonComponent::class],
    replaces = [RegistrationModule::class],
)
object TestRegistrationModule {

    @Provides
    @Singleton
    fun provideRegistrationGateway(
        repository: RegistrationRepository,
    ): RegistrationGateway = if (shouldUseLiveGateway()) {
        repository
    } else {
        ImmediateRegistrationGateway()
    }
}

private class ImmediateRegistrationGateway : RegistrationGateway {
    private val deviceIdRef = AtomicReference<String?>(null)
    private val lastAgentRef = AtomicReference<String?>(null)
    private val forcedDeviceId = resolveForcedDeviceId()

    override suspend fun ensureDeviceId(): String {
        val resolved = deviceIdRef.updateAndGet { current ->
            current ?: forcedDeviceId ?: UUID.randomUUID().toString()
        }!!
        logInfo(
            event = "registration_fake.ensure_device_id",
            fields = mapOf("deviceId" to resolved),
        )
        return resolved
    }

    override suspend fun register(deviceId: String, descriptor: DeviceDescriptor): RegistrationStatus {
        deviceIdRef.updateAndGet { current ->
            current ?: deviceId
        }
        val agentName = descriptor.label?.takeIf { it.isNotBlank() } ?: DEFAULT_AGENT_NAME
        lastAgentRef.set(agentName)
        logInfo(
            event = "registration_fake.register",
            fields = mapOf(
                "deviceId" to deviceId,
                "agentName" to agentName,
                "descriptorLabel" to descriptor.label,
            ),
        )
        return RegistrationStatus.Approved(
            agentName = agentName,
            message = "Approved instantly by instrumentation fake.",
        )
    }

    override suspend fun lastKnownAgent(): String? {
        val agent = lastAgentRef.get()
        logInfo(
            event = "registration_fake.last_agent",
            fields = mapOf("agentName" to agent),
        )
        return agent
    }

    companion object {
        private const val DEFAULT_AGENT_NAME = "Ringdown Instrumentation Agent"
        private const val TAG = "TestRegistrationModule"
        private const val LIVE_REG_VALUE = "live"
        private const val PRODUCTION_REG_VALUE = "production"
        private const val ENV_REG_MODE = "RINGDOWN_TEST_REGISTRATION_MODE"
        private const val ENV_LIVE_DEVICE_ID = "LIVE_TEST_MOBILE_DEVICE_ID"
        private const val DEFAULT_DEVICE_ID = "instrumentation-device"

        private fun logInfo(event: String, fields: Map<String, Any?>) {
            val builder = StringBuilder()
            builder.append("{\"severity\":\"INFO\",\"event\":\"").append(event).append("\"")
            for ((key, value) in fields) {
                builder.append(",\"").append(key).append("\":\"").append(value?.toString()?.replace("\"", "'") ?: "null").append("\"")
            }
            builder.append("}")
            Log.i(TAG, builder.toString())
        }

        internal fun shouldUseLiveGateway(): Boolean {
            val argument = InstrumentationRegistry.getArguments()
                .getString(TEST_REGISTRATION_MODE_ARGUMENT)
                ?.lowercase(Locale.US)
            val property = System.getProperty(TEST_REGISTRATION_MODE_PROPERTY)?.lowercase(Locale.US)
            val env = System.getenv(ENV_REG_MODE)?.lowercase(Locale.US)
            val result = listOf(argument, property, env).any { value ->
                value == LIVE_REG_VALUE || value == PRODUCTION_REG_VALUE
            }
            logInfo(
                event = "registration_gateway_mode",
                fields = mapOf(
                    "argument" to argument,
                    "property" to property,
                    "env" to env,
                    "live" to result,
                ),
            )
            return result
        }

        private fun resolveForcedDeviceId(): String? {
            val argument = InstrumentationRegistry.getArguments()
                .getString(TEST_LIVE_DEVICE_ID_ARGUMENT)
                ?.takeIf { it.isNotBlank() }
            val property = System.getProperty(TEST_LIVE_DEVICE_ID_PROPERTY)?.takeIf { it.isNotBlank() }
            val env = System.getenv(ENV_LIVE_DEVICE_ID)?.takeIf { it.isNotBlank() }
            return argument ?: property ?: env ?: DEFAULT_DEVICE_ID
        }
    }
}

private fun shouldUseLiveGateway(): Boolean = ImmediateRegistrationGateway.shouldUseLiveGateway()
