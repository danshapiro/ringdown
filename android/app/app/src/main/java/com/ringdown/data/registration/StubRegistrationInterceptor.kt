package com.ringdown.data.registration

import java.util.concurrent.atomic.AtomicInteger
import javax.inject.Inject
import javax.inject.Singleton
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody

@Singleton
class StubRegistrationInterceptor @Inject constructor() : Interceptor {

    private val registerCallCount = AtomicInteger(0)

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        return if (request.url.encodedPath == "/v1/mobile/devices/register") {
            val count = registerCallCount.incrementAndGet()
            val status = if (count >= APPROVAL_THRESHOLD) {
                "APPROVED"
            } else {
                "PENDING"
            }
            val json = """{"status":"$status"}"""
            Response.Builder()
                .code(200)
                .message("OK")
                .protocol(Protocol.HTTP_1_1)
                .request(request)
                .body(json.toResponseBody(JSON_MEDIA_TYPE))
                .build()
        } else {
            chain.proceed(request)
        }
    }

    companion object {
        private const val APPROVAL_THRESHOLD = 3
        private val JSON_MEDIA_TYPE = "application/json".toMediaType()
    }
}
