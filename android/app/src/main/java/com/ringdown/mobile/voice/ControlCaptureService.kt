package com.ringdown.mobile.voice

import android.app.Activity
import android.app.ForegroundServiceStartNotAllowedException
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.ringdown.mobile.MainActivity
import com.ringdown.mobile.R
import android.content.pm.ServiceInfo
import java.util.concurrent.CopyOnWriteArraySet
import java.util.concurrent.atomic.AtomicReference

/**
 * Foreground service that owns the MediaProjection lifecycle for the control harness.
 */
class ControlCaptureService : Service() {

    private val projectionManager: MediaProjectionManager? by lazy(LazyThreadSafetyMode.NONE) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            getSystemService(Context.MEDIA_PROJECTION_SERVICE) as? MediaProjectionManager
        } else {
            null
        }
    }

    override fun onCreate() {
        super.onCreate()
        ensureNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> handleStart(intent)
            ACTION_STOP -> handleStop()
            else -> Log.w(TAG, "Received unknown intent action: ${intent?.action}")
        }
        return START_STICKY
    }

    override fun onDestroy() {
        try {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } catch (_: Exception) {
            // ignore if service was not in foreground
        }
        super.onDestroy()
        releaseCurrentProjection()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun handleStart(startIntent: Intent) {
        val notification = buildNotification()
        val enteredForeground = try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
            true
        } catch (error: ForegroundServiceStartNotAllowedException) {
            Log.e(TAG, "Foreground service start not allowed for capture service", error)
            false
        } catch (error: SecurityException) {
            Log.e(TAG, "Unable to start foreground capture service due to security error", error)
            false
        } catch (error: IllegalStateException) {
            Log.e(TAG, "Unable to start foreground capture service; state violation", error)
            false
        } catch (error: Exception) {
            Log.e(TAG, "Unexpected failure entering foreground state", error)
            false
        }
        if (!enteredForeground) {
            notifyListeners(null)
            stopSelf()
            return
        }

        val storedExtras = startIntentExtras.getAndSet(null)
        val resultCode = storedExtras?.first ?: startIntent.getIntExtra(EXTRA_RESULT_CODE, Activity.RESULT_CANCELED)
        val resultData = storedExtras?.second ?: if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            startIntent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
        } else {
            @Suppress("DEPRECATION")
            startIntent.getParcelableExtra(EXTRA_RESULT_DATA)
        }
        Log.i(TAG, "Starting control capture service; resultCode=$resultCode hasData=${resultData != null}")
        if (resultCode != Activity.RESULT_OK || resultData == null) {
            Log.w(TAG, "MediaProjection permission denied (resultCode=$resultCode)")
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return
        }

        val manager = projectionManager
        if (manager == null) {
            Log.w(TAG, "MediaProjectionManager unavailable; stopping service")
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return
        }

        val projection = try {
            manager.getMediaProjection(resultCode, resultData)
        } catch (error: SecurityException) {
            Log.w(TAG, "Unable to obtain MediaProjection", error)
            null
        }

        if (projection == null) {
            Log.w(TAG, "MediaProjection acquisition returned null")
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return
        }

        val activeProjection = currentProjectionRef.get()
        if (activeProjection == null) {
            attachProjection(projection)
        } else {
            attachProjection(projection)
        }
        notifyListeners(currentProjectionRef.get())
    }

    private fun handleStop() {
        try {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } catch (_: Exception) {
            Log.w(TAG, "Failed to stop foreground state; service may not have been started")
        }
        stopSelf()
    }

    private fun attachProjection(projection: MediaProjection) {
        val newHandle = ProjectionHandle(projection, ProjectionCallback())
        val previousHandle = currentHandleRef.getAndSet(newHandle)
        previousHandle?.let { handle ->
            try {
                handle.projection.unregisterCallback(handle.callback)
            } catch (_: Exception) {
                // ignore unregister failure
            }
            try {
                handle.projection.stop()
            } catch (_: Exception) {
                // ignore stop failure
            }
        }

        try {
            projection.registerCallback(newHandle.callback, mainHandler)
        } catch (error: Exception) {
            Log.w(TAG, "Unable to register MediaProjection callback", error)
        }

        currentProjectionRef.set(projection)
        Log.i(TAG, "MediaProjection attached to capture service")
    }

    private fun releaseCurrentProjection() {
        val handle = currentHandleRef.getAndSet(null) ?: return
        try {
            handle.projection.unregisterCallback(handle.callback)
        } catch (_: Exception) {
            // ignore
        }
        try {
            handle.projection.stop()
        } catch (_: Exception) {
            // ignore
        }
        currentProjectionRef.set(null)
        notifyListeners(null)
    }

    private fun buildNotification(): Notification {
        val intent = Intent(this, MainActivity::class.java)
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.control_capture_notification_title))
            .setContentText(getString(R.string.control_capture_notification_text))
            .setSmallIcon(R.drawable.ic_control_capture)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return
        }
        val manager = getSystemService(NotificationManager::class.java) ?: return
        val existing = manager.getNotificationChannel(CHANNEL_ID)
        if (existing != null) {
            return
        }
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.control_capture_notification_channel),
            NotificationManager.IMPORTANCE_LOW,
        )
        channel.setShowBadge(false)
        manager.createNotificationChannel(channel)
    }

    private fun notifyListeners(projection: MediaProjection?) {
        Log.i(TAG, "Notifying ${listeners.size} listeners of MediaProjection update (active=${projection != null})")
        listeners.forEach { listener ->
            try {
                listener(projection)
            } catch (error: Exception) {
                Log.w(TAG, "Projection listener threw", error)
            }
        }
    }

    private inner class ProjectionCallback : MediaProjection.Callback() {
        override fun onStop() {
            mainHandler.post {
                Log.i(TAG, "MediaProjection revoked by system")
                releaseCurrentProjection()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
    }

    private data class ProjectionHandle(
        val projection: MediaProjection,
        val callback: ProjectionCallback,
    )

    companion object {
        private const val TAG = "ControlCaptureService"
        private const val ACTION_START = "com.ringdown.mobile.voice.action.START_CAPTURE_SERVICE"
        private const val ACTION_STOP = "com.ringdown.mobile.voice.action.STOP_CAPTURE_SERVICE"
        private const val EXTRA_RESULT_CODE = "extra_result_code"
        private const val EXTRA_RESULT_DATA = "extra_result_data"
        private const val CHANNEL_ID = "control_capture"
        private const val NOTIFICATION_ID = 1001

        private val startIntentExtras = AtomicReference<Pair<Int, Intent>?>()
        private val currentProjectionRef = AtomicReference<MediaProjection?>()
        private val currentHandleRef = AtomicReference<ProjectionHandle?>()
        private val mainHandler: Handler by lazy(LazyThreadSafetyMode.NONE) {
            Handler(Looper.getMainLooper())
        }
        private val listeners = CopyOnWriteArraySet<(MediaProjection?) -> Unit>()

        fun start(context: Context, resultCode: Int, resultData: Intent) {
            startIntentExtras.set(resultCode to resultData)
            val intent = Intent(context, ControlCaptureService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_RESULT_CODE, resultCode)
                putExtra(EXTRA_RESULT_DATA, resultData)
            }
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, ControlCaptureService::class.java))
        }

        fun registerListener(listener: (MediaProjection?) -> Unit) {
            listeners.add(listener)
            listener(currentProjectionRef.get())
        }

        fun unregisterListener(listener: (MediaProjection?) -> Unit) {
            listeners.remove(listener)
        }

        fun currentProjection(): MediaProjection? = currentProjectionRef.get()
    }
}
