package com.ringdown.data.device

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.ringdown.di.IoDispatcher
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.withContext

private val Context.deviceDataStore: DataStore<Preferences> by preferencesDataStore(
    name = "ringdown_device"
)

class DeviceIdStorage @Inject constructor(
    @ApplicationContext private val context: Context,
    @IoDispatcher private val ioDispatcher: CoroutineDispatcher
) {

    suspend fun getOrCreate(): String = withContext(ioDispatcher) {
        val key = KEY_DEVICE_ID

        val existing = context.deviceDataStore.data
            .map { preferences -> preferences[key] }
            .first()

        if (existing != null) {
            existing
        } else {
            val newId = generateDeviceId()
            context.deviceDataStore.edit { prefs ->
                prefs[key] = newId
            }
            newId
        }
    }

    private fun generateDeviceId(): String = java.util.UUID.randomUUID().toString()

    companion object {
        private val KEY_DEVICE_ID = stringPreferencesKey("device_id")
    }
}
