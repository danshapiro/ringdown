package com.ringdown

import java.util.concurrent.atomic.AtomicReference

object DebugFeatureFlags {
    private val registrationStubOverride = AtomicReference<Boolean?>(null)
    private val voiceTransportStubOverride = AtomicReference<Boolean?>(null)

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
}
