package com.ringdown.mobile.data

import com.ringdown.mobile.BuildConfig
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
open class BackendEnvironment @Inject constructor() {

    open fun baseUrl(): String {
        return if (BuildConfig.DEBUG) {
            BuildConfig.STAGING_BACKEND_BASE_URL
        } else {
            BuildConfig.PRODUCTION_BACKEND_BASE_URL
        }
    }
}
