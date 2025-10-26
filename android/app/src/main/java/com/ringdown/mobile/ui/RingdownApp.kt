@file:OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)

package com.ringdown.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import com.ringdown.mobile.domain.RegistrationStatus
import com.ringdown.mobile.R
import com.ringdown.mobile.voice.TranscriptMessage
import com.ringdown.mobile.voice.VoiceConnectionState

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RingdownApp(
    state: MainUiState,
    onReconnect: () -> Unit,
    onHangUp: () -> Unit,
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
            when (val voiceState = state.voiceState) {
                is VoiceConnectionState.Connected -> VoiceSessionContent(
                    transcripts = voiceState.transcripts,
                    onHangUp = onHangUp,
                )

                is VoiceConnectionState.Connecting -> VoiceSessionConnecting(onHangUp)

                else -> when (val status = state.registrationStatus) {
                    is RegistrationStatus.Denied -> DeniedContent(status.message)
                    else -> IdleContent(
                        state = state,
                        onReconnect = onReconnect,
                        onOpenChat = onOpenChat,
                    )
                }
            }

            if (state.isLoading) {
                LoadingOverlay()
            }

            val pendingStatus = state.registrationStatus
            if (pendingStatus is RegistrationStatus.Pending) {
                PendingApprovalDialog(
                    deviceId = state.deviceId,
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
    deviceId: String,
    message: String,
    onCheckAgain: () -> Unit,
) {
    AlertDialog(
        modifier = Modifier.testTag("pending-dialog"),
        onDismissRequest = { },
        title = { Text(text = stringResource(id = R.string.pending_dialog_title)) },
        text = {
            Column {
                Text(
                    text = stringResource(id = R.string.pending_dialog_body, deviceId),
                    style = MaterialTheme.typography.bodyMedium,
                )
                if (message.isNotBlank()) {
                    Spacer(modifier = Modifier.height(12.dp))
                    Text(
                        text = message,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
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

@Composable
private fun VoiceSessionConnecting(onHangUp: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 32.dp)
            .testTag("voice-connecting"),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        CircularProgressIndicator()
        Spacer(modifier = Modifier.height(16.dp))
        Text(
            text = stringResource(id = R.string.voice_connecting_message),
            style = MaterialTheme.typography.bodyLarge,
            textAlign = TextAlign.Center,
        )
        Spacer(modifier = Modifier.height(24.dp))
        Button(onClick = onHangUp) {
            Text(text = stringResource(id = R.string.hang_up_button))
        }
    }
}

@Composable
private fun VoiceSessionContent(
    transcripts: List<TranscriptMessage>,
    onHangUp: () -> Unit,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 24.dp, vertical = 32.dp)
            .testTag("voice-active"),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(bottom = 72.dp),
        ) {
            Text(
                text = stringResource(id = R.string.voice_session_title),
                style = MaterialTheme.typography.headlineSmall,
                modifier = Modifier.align(Alignment.CenterHorizontally),
            )
            Spacer(modifier = Modifier.height(16.dp))
            if (transcripts.isEmpty()) {
                Box(
                    modifier = Modifier
                        .fillMaxSize(),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = stringResource(id = R.string.voice_transcript_empty),
                        style = MaterialTheme.typography.bodyMedium,
                        textAlign = TextAlign.Center,
                    )
                }
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    items(transcripts) { transcript ->
                        TranscriptBubble(transcript)
                    }
                }
            }
        }
        Button(onClick = onHangUp, modifier = Modifier.align(Alignment.BottomCenter)) {
            Text(text = stringResource(id = R.string.hang_up_button))
        }
    }
}

@Composable
private fun TranscriptBubble(message: TranscriptMessage) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 4.dp),
    ) {
        Text(
            text = message.speaker.uppercase(),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.primary,
        )
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = message.text,
            style = MaterialTheme.typography.bodyLarge,
        )
    }
}
