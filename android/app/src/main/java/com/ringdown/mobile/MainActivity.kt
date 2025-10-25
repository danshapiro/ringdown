package com.ringdown.mobile

import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.core.content.PermissionChecker
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import com.ringdown.mobile.ui.MainViewModel
import com.ringdown.mobile.ui.RingdownApp
import com.ringdown.mobile.ui.theme.RingdownTheme
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        setContent {
            val context = LocalContext.current
            val viewModel: MainViewModel = viewModel()
            val state by viewModel.state.collectAsStateWithLifecycle()

            val permissionLauncher = rememberLauncherForActivityResult(
                ActivityResultContracts.RequestPermission(),
            ) { granted ->
                viewModel.onPermissionResult(granted)
            }

            LaunchedEffect(Unit) {
                val granted = isMicrophoneGranted()
                viewModel.onPermissionResult(granted, fromUserAction = false)
            }

            RingdownTheme(useDarkTheme = isSystemInDarkTheme()) {
                RingdownApp(
                    state = state,
                    onReconnect = {
                        if (isMicrophoneGranted()) {
                            viewModel.onPermissionResult(true)
                            Toast.makeText(
                                context,
                                "Voice session coming soon.",
                                Toast.LENGTH_SHORT,
                            ).show()
                        } else {
                            permissionLauncher.launch(android.Manifest.permission.RECORD_AUDIO)
                        }
                    },
                    onOpenChat = {
                        Toast.makeText(
                            context,
                            "Chat mode coming soon.",
                            Toast.LENGTH_SHORT,
                        ).show()
                    },
                    onCheckAgain = viewModel::onCheckAgainClicked,
                    onErrorDismissed = viewModel::acknowledgeError,
                )
            }
        }
    }

    private fun ComponentActivity.isMicrophoneGranted(): Boolean {
        return ContextCompat.checkSelfPermission(
            this,
            android.Manifest.permission.RECORD_AUDIO,
        ) == PermissionChecker.PERMISSION_GRANTED
    }
}
