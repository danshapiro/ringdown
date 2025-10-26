package com.ringdown.mobile.ui

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.ringdown.mobile.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class VoicePermissionCopyTest {

    @Test
    fun voicePermissionReminderIsGeneric() {
        val context = ApplicationProvider.getApplicationContext<Context>()

        val message = context.getString(R.string.voice_permissions_required)

        assertThat(message).isEqualTo("Microphone permission required.")
    }
}
