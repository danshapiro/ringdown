package com.ringdown.mobile.di

import com.ringdown.mobile.data.DeviceDescriptor
import android.util.Log
import androidx.test.platform.app.InstrumentationRegistry
import com.ringdown.mobile.data.RegistrationGateway
import com.ringdown.mobile.data.RegistrationRepository
import com.ringdown.mobile.domain.RegistrationStatus
import dagger.Module
import dagger.Provides
import dagger.hilt.components.SingletonComponent
import dagger.hilt.testing.TestInstallIn
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

    override suspend fun ensureDeviceId(): String {
        return deviceIdRef.updateAndGet { current ->
            current ?: UUID.randomUUID().toString()
        }!!
    }

    override suspend fun register(deviceId: String, descriptor: DeviceDescriptor): RegistrationStatus {
        deviceIdRef.updateAndGet { current ->
            current ?: deviceId
        }
        val agentName = descriptor.label?.takeIf { it.isNotBlank() } ?: DEFAULT_AGENT_NAME
        lastAgentRef.set(agentName)
        return RegistrationStatus.Approved(
            agentName = agentName,
            message = "Approved instantly by instrumentation fake.",
        )
    }

    override suspend fun lastKnownAgent(): String? {
        return lastAgentRef.get()
    }

    companion object {
        private const val DEFAULT_AGENT_NAME = "Ringdown Instrumentation Agent"
    }
}

private fun shouldUseLiveGateway(): Boolean {
    val property = System.getProperty("ringdown.test.registration_mode")?.lowercase()
    val env = System.getenv("RINGDOWN_TEST_REGISTRATION_MODE")?.lowercase()
    val argument = runCatching {
        InstrumentationRegistry.getArguments().getString("registrationMode")
    }.getOrNull()?.lowercase()
    val result = when {
        argument == "live" || argument == "production" -> true
        property == "live" || property == "production" -> true
        env == "live" || env == "production" -> true
        else -> false
    }
    Log.i(
        "TestRegistrationModule",
        "{\"severity\":\"INFO\",\"event\":\"registration_gateway_mode\",\"property\":\"$property\",\"env\":\"$env\",\"argument\":\"$argument\",\"live\":$result}",
    )
    return result
}
