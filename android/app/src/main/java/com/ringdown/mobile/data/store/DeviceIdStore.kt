package com.ringdown.mobile.data.store

import android.content.Context
import android.util.Log
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStoreFile
import com.ringdown.mobile.BuildConfig
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
            logInfo(
                event = "device_id.loaded",
                fields = mapOf("deviceId" to existing),
            )
            return existing
        }
        val newId = UUID.randomUUID().toString()
        dataStore.edit { prefs ->
            prefs[DEVICE_ID_KEY] = newId
        }
        logInfo(
            event = "device_id.generated",
            fields = mapOf("deviceId" to newId),
        )
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
        private const val TAG = "DeviceIdStore"

        fun createDataStore(context: Context): DataStore<Preferences> {
            return androidx.datastore.preferences.core.PreferenceDataStoreFactory.create(
                produceFile = { context.preferencesDataStoreFile(DEVICE_DATASTORE_NAME) },
            )
        }
    }

    private fun logInfo(event: String, fields: Map<String, Any?> = emptyMap()) {
        if (!BuildConfig.DEBUG) {
            return
        }
        Log.i(TAG, buildLogPayload("INFO", event, fields))
    }

    private fun buildLogPayload(level: String, event: String, fields: Map<String, Any?>): String {
        val builder = StringBuilder()
        builder.append("{\"severity\":\"").append(level).append("\",\"event\":\"").append(event).append("\"")
        for ((key, value) in fields) {
            builder.append(",\"").append(key).append("\":\"").append(value?.toString()?.replace("\"", "'") ?: "null").append("\"")
        }
        builder.append("}")
        return builder.toString()
    }
}
