package com.ringdown.mobile.di

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.core.DataStoreFactory
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.datastore.preferences.core.Preferences
import com.ringdown.mobile.data.store.DeviceIdStore
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object DataStoreModule {

    @Volatile
    private var deviceDataStore: DataStore<Preferences>? = null

    @Provides
    @Singleton
    fun provideDeviceDataStore(
        @ApplicationContext context: Context,
    ): DataStore<Preferences> {
        val appContext = context.applicationContext
        return deviceDataStore ?: synchronized(this) {
            deviceDataStore ?: PreferenceDataStoreFactory.create {
                appContext.filesDir.resolve("datastore")
                    .also { it.mkdirs() }
                    .resolve("ringdown_device.preferences_pb")
            }.also { created ->
                deviceDataStore = created
            }
        }
    }
}
