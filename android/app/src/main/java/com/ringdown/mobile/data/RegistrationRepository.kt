package com.ringdown.mobile.data

import android.os.Build
import com.ringdown.mobile.BuildConfig
import com.ringdown.mobile.data.remote.RegisterDeviceRequest
import com.ringdown.mobile.data.remote.RegistrationApi
import com.ringdown.mobile.data.remote.RegisterDeviceResponse
import com.ringdown.mobile.data.store.DeviceIdStore
import com.ringdown.mobile.di.IoDispatcher
import com.ringdown.mobile.domain.RegistrationStatus
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.withContext
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

data class DeviceDescriptor(
    val label: String?,
    val platform: String,
    val model: String?,
    val appVersion: String?,
)

interface RegistrationGateway {
    suspend fun ensureDeviceId(): String
    suspend fun register(deviceId: String, descriptor: DeviceDescriptor): RegistrationStatus
    suspend fun lastKnownAgent(): String?
}

@Singleton
class RegistrationRepository @Inject constructor(
    private val api: RegistrationApi,
    private val deviceIdStore: DeviceIdStore,
    private val backendEnvironment: BackendEnvironment,
    @IoDispatcher private val dispatcher: CoroutineDispatcher,
) : RegistrationGateway {

    private val stubAttempts = ConcurrentHashMap<String, Int>()

    override suspend fun ensureDeviceId(): String = withContext(dispatcher) {
        deviceIdStore.getOrCreateId()
    }

    override suspend fun register(deviceId: String, descriptor: DeviceDescriptor): RegistrationStatus =
        withContext(dispatcher) {
            if (backendEnvironment.useStubRegistration) {
                invokeStub(deviceId)
            } else {
                val response = api.registerDevice(
                    RegisterDeviceRequest(
                        deviceId = deviceId,
                        label = descriptor.label,
                        platform = descriptor.platform,
                        model = descriptor.model,
                        appVersion = descriptor.appVersion,
                    ),
                )
                mapResponse(deviceId, response)
            }
        }

    private suspend fun mapResponse(
        deviceId: String,
        response: RegisterDeviceResponse,
    ): RegistrationStatus {
        val message = response.message.orEmpty()
        return when (response.status.uppercase(Locale.US)) {
            "APPROVED" -> {
                deviceIdStore.saveLastSuccessfulAgent(response.agent)
                RegistrationStatus.Approved(
                    agentName = response.agent,
                    message = response.message,
                )
            }

            "PENDING" -> RegistrationStatus.Pending(
                message = message.ifBlank { "Awaiting administrator approval" },
                pollAfterSeconds = response.pollAfterSeconds,
            )

            "DENIED" -> RegistrationStatus.Denied(
                message = message.ifBlank { "Device denied by administrator" },
            )

            else -> throw RegistrationException("Unexpected registration status '${response.status}'")
        }
    }

    private suspend fun invokeStub(deviceId: String): RegistrationStatus {
        val attempt = stubAttempts.merge(deviceId, 1) { current, _ -> current + 1 } ?: 1
        val threshold = backendEnvironment.stubApprovalThreshold.coerceAtLeast(1)
        return if (attempt >= threshold) {
            val agentName = "debug-agent"
            deviceIdStore.saveLastSuccessfulAgent(agentName)
            RegistrationStatus.Approved(
                agentName = agentName,
                message = "Device approved (stub)",
            )
        } else {
            RegistrationStatus.Pending(
                message = "Awaiting administrator approval",
                pollAfterSeconds = 5,
            )
        }
    }

    override suspend fun lastKnownAgent(): String? = withContext(dispatcher) {
        deviceIdStore.lastSuccessfulAgent()
    }

    companion object {
        fun buildDefaultDescriptor(): DeviceDescriptor {
            val model = "${Build.MANUFACTURER} ${Build.MODEL}".trim()
            return DeviceDescriptor(
                label = model.ifBlank { null },
                platform = "android",
                model = model.ifBlank { null },
                appVersion = BuildConfig.VERSION_NAME,
            )
        }
    }
}

class RegistrationException(message: String, cause: Throwable? = null) : Exception(message, cause)
