package com.ringdown.data.registration

import com.ringdown.di.StubApprovalThreshold
import java.util.concurrent.atomic.AtomicInteger
import javax.inject.Inject
import javax.inject.Singleton
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody

@Singleton
class StubRegistrationInterceptor @Inject constructor(
    @StubApprovalThreshold private val approvalThreshold: Int
) : Interceptor {

    private val registerCallCount = AtomicInteger(0)

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        if (request.url.encodedPath != "/v1/mobile/devices/register") {
            return chain.proceed(request)
        }

        val count = registerCallCount.incrementAndGet()
        val shouldApprove = approvalThreshold <= 0 || count >= approvalThreshold
        val status = if (shouldApprove) STATUS_APPROVED else STATUS_PENDING
        val pollAfterSeconds = if (shouldApprove) null else DEFAULT_POLL_AFTER_SECONDS
        val message = if (shouldApprove) {
            "Device approved"
        } else {
            "Awaiting administrator approval"
        }

        val body = buildJsonResponse(status, message, pollAfterSeconds)
            .toResponseBody(JSON_MEDIA_TYPE)

        return Response.Builder()
            .code(200)
            .message("OK")
            .protocol(Protocol.HTTP_1_1)
            .request(request)
            .body(body)
            .build()
    }

    private fun buildJsonResponse(
        status: String,
        message: String,
        pollAfterSeconds: Long?
    ): String {
        return if (pollAfterSeconds != null) {
            """{"status":"$status","message":"$message","pollAfterSeconds":$pollAfterSeconds}"""
        } else {
            """{"status":"$status","message":"$message"}"""
        }
    }

    companion object {
        private const val STATUS_APPROVED = "APPROVED"
        private const val STATUS_PENDING = "PENDING"
        private const val DEFAULT_POLL_AFTER_SECONDS = 5L
        private val JSON_MEDIA_TYPE = "application/json".toMediaType()
    }
}
