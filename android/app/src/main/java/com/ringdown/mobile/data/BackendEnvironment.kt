package com.ringdown.mobile.data

import com.ringdown.mobile.BuildConfig
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class BackendEnvironment @Inject constructor() {

    fun baseUrl(): String {
        return if (BuildConfig.DEBUG) {
            BuildConfig.STAGING_BACKEND_BASE_URL
        } else {
            BuildConfig.PRODUCTION_BACKEND_BASE_URL
        }
    }

    val useStubRegistration: Boolean
        get() = BuildConfig.DEBUG_USE_REGISTRATION_STUB && BuildConfig.DEBUG

    val stubApprovalThreshold: Int
        get() = BuildConfig.DEBUG_STUB_APPROVAL_THRESHOLD
}
