package com.ringdown

import java.util.concurrent.atomic.AtomicReference

object DebugFeatureFlags {
    private val registrationStubOverride = AtomicReference<Boolean?>(null)
    private val voiceTransportStubOverride = AtomicReference<Boolean?>(null)
    private val backendBaseUrlOverride = AtomicReference<String?>(null)
    private val deviceIdOverride = AtomicReference<String?>(null)

    fun overrideRegistrationStub(value: Boolean?) {
        registrationStubOverride.set(value)
    }

    fun shouldUseRegistrationStub(defaultValue: Boolean): Boolean {
        return registrationStubOverride.get() ?: defaultValue
    }

    fun overrideVoiceTransportStub(value: Boolean?) {
        voiceTransportStubOverride.set(value)
    }

    fun shouldUseVoiceTransportStub(defaultValue: Boolean): Boolean {
        return voiceTransportStubOverride.get() ?: defaultValue
    }

    fun overrideBackendBaseUrl(value: String?) {
        backendBaseUrlOverride.set(value)
    }

    fun backendBaseUrlOrDefault(defaultValue: String): String {
        return backendBaseUrlOverride.get() ?: defaultValue
    }

    fun overrideDeviceId(value: String?) {
        deviceIdOverride.set(value)
    }

    fun deviceIdOverride(): String? = deviceIdOverride.get()

    fun clearOverrides() {
        overrideBackendBaseUrl(null)
        overrideRegistrationStub(null)
        overrideVoiceTransportStub(null)
        overrideDeviceId(null)
    }
}
