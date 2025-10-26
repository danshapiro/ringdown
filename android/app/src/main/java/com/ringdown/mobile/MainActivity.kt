package com.ringdown.mobile

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ringdown.mobile.ui.MainUiState
import com.ringdown.mobile.ui.MainViewModel
import com.ringdown.mobile.ui.RingdownApp
import com.ringdown.mobile.ui.theme.RingdownTheme
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val viewModel: MainViewModel = hiltViewModel()
            val uiState by viewModel.state.collectAsStateWithLifecycle()
            val context = LocalContext.current

            val permissions = remember { requiredVoicePermissions() }
            val permissionLauncher = rememberLauncherForActivityResult(
                contract = ActivityResultContracts.RequestMultiplePermissions(),
            ) { result ->
                val granted = permissions.all { permission -> result[permission] == true }
                viewModel.onPermissionResult(granted)
            }

            LaunchedEffect(Unit) {
                val granted = permissions.all { permission ->
                    ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED
                }
                viewModel.onPermissionResult(granted, fromUserAction = false)
            }

            LaunchedEffect(uiState.permissionRequestVersion) {
                if (uiState.permissionRequestVersion <= 0) return@LaunchedEffect
                val granted = permissions.all { permission ->
                    ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED
                }
                if (!granted) {
                    permissionLauncher.launch(permissions)
                }
            }

            RingdownTheme(useDarkTheme = isSystemInDarkTheme()) {
                MainScreenContent(
                    viewModel = viewModel,
                    state = uiState,
                    onReconnect = {
                        val granted = permissions.all { permission ->
                            ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED
                        }
                        if (granted) {
                            viewModel.onPermissionResult(true)
                            viewModel.startVoiceSession()
                        } else {
                            viewModel.startVoiceSession()
                        }
                    },
                    onHangUp = viewModel::stopVoiceSession,
                )
            }
        }
    }
}

@Composable
private fun MainScreenContent(
    viewModel: MainViewModel,
    state: MainUiState,
    onReconnect: () -> Unit,
    onHangUp: () -> Unit,
) {
    RingdownApp(
        state = state,
        onReconnect = onReconnect,
        onHangUp = onHangUp,
        onOpenChat = { /* Chat flow to follow in ringdown-11 */ },
        onCheckAgain = viewModel::onCheckAgainClicked,
        onErrorDismissed = viewModel::acknowledgeError,
    )
}

private fun requiredVoicePermissions(): Array<String> {
    val required = mutableListOf(Manifest.permission.RECORD_AUDIO)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        required += Manifest.permission.BLUETOOTH_CONNECT
    }
    return required.toTypedArray()
}
