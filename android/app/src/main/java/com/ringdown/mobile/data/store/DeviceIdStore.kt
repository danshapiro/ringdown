package com.ringdown.mobile.data.store

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStoreFile
import kotlinx.coroutines.flow.first
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

private const val DEVICE_DATASTORE_NAME = "ringdown_device.preferences_pb"

@Singleton
class DeviceIdStore @Inject constructor(
    private val dataStore: DataStore<Preferences>,
) {

    suspend fun getOrCreateId(): String {
        val preferences = dataStore.data.first()
        val existing = preferences[DEVICE_ID_KEY]
        if (!existing.isNullOrBlank()) {
            return existing
        }
        val newId = UUID.randomUUID().toString()
        dataStore.edit { prefs ->
            prefs[DEVICE_ID_KEY] = newId
        }
        return newId
    }

    suspend fun saveLastSuccessfulAgent(agent: String?) {
        dataStore.edit { prefs ->
            if (agent.isNullOrBlank()) {
                prefs.remove(LAST_AGENT_KEY)
            } else {
                prefs[LAST_AGENT_KEY] = agent
            }
        }
    }

    suspend fun lastSuccessfulAgent(): String? {
        val prefs = dataStore.data.first()
        return prefs[LAST_AGENT_KEY]
    }

    suspend fun currentAuthToken(): String? {
        val prefs = dataStore.data.first()
        return prefs[AUTH_TOKEN_KEY]
    }

    suspend fun currentResumeToken(): String? {
        val prefs = dataStore.data.first()
        return prefs[RESUME_TOKEN_KEY]
    }

    suspend fun updateAuthToken(token: String?) {
        dataStore.edit { prefs ->
            if (token.isNullOrBlank()) {
                prefs.remove(AUTH_TOKEN_KEY)
            } else {
                prefs[AUTH_TOKEN_KEY] = token
            }
        }
    }

    suspend fun updateResumeToken(token: String?) {
        dataStore.edit { prefs ->
            if (token.isNullOrBlank()) {
                prefs.remove(RESUME_TOKEN_KEY)
            } else {
                prefs[RESUME_TOKEN_KEY] = token
            }
        }
    }

    companion object {
        private val DEVICE_ID_KEY = stringPreferencesKey("device_id")
        private val LAST_AGENT_KEY = stringPreferencesKey("last_agent")
        private val AUTH_TOKEN_KEY = stringPreferencesKey("auth_token")
        private val RESUME_TOKEN_KEY = stringPreferencesKey("resume_token")

        fun createDataStore(context: Context): DataStore<Preferences> {
            return androidx.datastore.preferences.core.PreferenceDataStoreFactory.create(
                produceFile = { context.preferencesDataStoreFile(DEVICE_DATASTORE_NAME) },
            )
        }
    }
}
