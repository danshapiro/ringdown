package com.ringdown.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.R

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RingdownApp(
    state: MainUiState,
    onReconnect: () -> Unit,
    onOpenChat: () -> Unit,
    onCheckAgain: () -> Unit,
    onErrorDismissed: () -> Unit,
) {
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(state.errorMessage) {
        val message = state.errorMessage
        if (message != null) {
            val result = snackbarHostState.showSnackbar(
                message = message,
                duration = SnackbarDuration.Short,
            )
            if (result != SnackbarResult.Dismissed) {
                onErrorDismissed()
            } else {
                onErrorDismissed()
            }
        }
    }

    Scaffold(
        snackbarHost = {
            SnackbarHost(hostState = snackbarHostState) { snackbarData ->
                Snackbar(snackbarData)
            }
        },
    ) { padding ->
        Surface(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            color = MaterialTheme.colorScheme.background,
        ) {
            when (val status = state.registrationStatus) {
                is RegistrationStatus.Denied -> DeniedContent(status.message)
                else -> IdleContent(
                    state = state,
                    onReconnect = onReconnect,
                    onOpenChat = onOpenChat,
                )
            }

            if (state.isLoading) {
                LoadingOverlay()
            }

            val pendingStatus = state.registrationStatus
            if (pendingStatus is RegistrationStatus.Pending) {
                PendingApprovalDialog(
                    message = pendingStatus.message,
                    onCheckAgain = onCheckAgain,
                )
            }
        }
    }
}

@Composable
private fun IdleContent(
    state: MainUiState,
    onReconnect: () -> Unit,
    onOpenChat: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 32.dp)
            .testTag("idle-screen"),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Button(onClick = onReconnect) {
            Text(text = stringResource(id = R.string.reconnect_button))
        }
        Spacer(modifier = Modifier.height(16.dp))
        Button(onClick = onOpenChat, enabled = false) {
            Text(text = stringResource(id = R.string.open_chat_button))
        }
        if (state.showMicrophoneReminder && !state.microphonePermissionGranted) {
            Spacer(modifier = Modifier.height(16.dp))
            Text(
                text = stringResource(id = R.string.voice_permissions_required),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.error,
                textAlign = TextAlign.Center,
            )
        }
    }
}

@Composable
private fun PendingApprovalDialog(
    message: String,
    onCheckAgain: () -> Unit,
) {
    AlertDialog(
        modifier = Modifier.testTag("pending-dialog"),
        onDismissRequest = { },
        title = { Text(text = stringResource(id = R.string.pending_dialog_title)) },
        text = {
            Text(
                text = message,
                style = MaterialTheme.typography.bodyMedium,
            )
        },
        confirmButton = {
            Button(onClick = onCheckAgain) {
                Text(text = stringResource(id = R.string.check_again))
            }
        },
    )
}

@Composable
private fun DeniedContent(message: String) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = "Access denied",
            style = MaterialTheme.typography.headlineSmall,
        )
        Spacer(modifier = Modifier.height(16.dp))
        Text(
            text = message,
            style = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun LoadingOverlay() {
    Column(
        modifier = Modifier
            .fillMaxSize(),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        CircularProgressIndicator()
    }
}
