@file:OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)

package com.ringdown.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import com.ringdown.mobile.chat.ChatConnectionState
import com.ringdown.mobile.chat.ChatMessage
import com.ringdown.mobile.chat.ChatMessageRole
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
    onCloseChat: () -> Unit,
    onChatInputChanged: (String) -> Unit,
    onSendChatMessage: () -> Unit,
    onChatVoiceSwitch: () -> Unit,
    onChatRetry: () -> Unit,
    onResetConversation: () -> Unit,
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
            if (state.isChatVisible) {
                ChatScreen(
                    chatState = state.chatState,
                    chatHistory = state.chatHistory,
                    inputValue = state.chatInput,
                    onInputChange = onChatInputChanged,
                    onSend = onSendChatMessage,
                    onClose = onCloseChat,
                    onSwitchToVoice = onChatVoiceSwitch,
                    onRetry = onChatRetry,
                    onReset = onResetConversation,
                )
            } else {
                when (val voiceState = state.voiceState) {
                    is VoiceConnectionState.Connected -> VoiceSessionContent(
                        transcripts = voiceState.transcripts,
                        chatHistory = state.chatHistory,
                        onHangUp = onHangUp,
                    )

                    is VoiceConnectionState.Connecting -> VoiceSessionConnecting(
                        onHangUp = onHangUp,
                        isReconnecting = state.isVoiceReconnecting,
                    )

                    else -> when (val status = state.registrationStatus) {
                        is RegistrationStatus.Denied -> DeniedContent(status.message)
                        else -> IdleContent(
                            state = state,
                            onReconnect = onReconnect,
                            onOpenChat = onOpenChat,
                        )
                    }
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
    val chatEnabled = state.registrationStatus is RegistrationStatus.Approved
    val connectLabel = if (state.registrationStatus is RegistrationStatus.Approved) {
        stringResource(id = R.string.reconnect_button)
    } else {
        stringResource(id = R.string.connect_button)
    }
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 32.dp)
            .testTag("idle-screen"),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Button(onClick = onReconnect, enabled = !state.isLoading) {
            Text(text = connectLabel)
        }
        Spacer(modifier = Modifier.height(16.dp))
        Button(onClick = onOpenChat, enabled = chatEnabled) {
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
private fun VoiceSessionConnecting(onHangUp: () -> Unit, isReconnecting: Boolean) {
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
        val messageRes = if (isReconnecting) {
            R.string.voice_reconnecting_message
        } else {
            R.string.voice_connecting_message
        }
        Text(
            text = stringResource(id = messageRes),
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
    chatHistory: List<ChatMessage>,
    onHangUp: () -> Unit,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 24.dp, vertical = 32.dp)
            .testTag("voice-active"),
    ) {
        val combinedTranscripts = remember(transcripts, chatHistory) {
            combineTranscriptHistory(chatHistory, transcripts)
        }
        val toolExpansionState = remember { mutableStateMapOf<String, Boolean>() }
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
            if (combinedTranscripts.isEmpty()) {
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
                    itemsIndexed(
                        items = combinedTranscripts,
                        key = { index, message -> transcriptKey(message, index) },
                    ) { index, transcript ->
                        val key = transcriptKey(transcript, index)
                        val expanded = toolExpansionState[key] ?: false
                        TranscriptBubble(
                            message = transcript,
                            isExpanded = expanded,
                            onToggleExpand = {
                                toolExpansionState[key] = !(toolExpansionState[key] ?: false)
                            },
                        )
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
private fun ChatScreen(
    chatState: ChatConnectionState,
    chatHistory: List<ChatMessage>,
    inputValue: String,
    onInputChange: (String) -> Unit,
    onSend: () -> Unit,
    onClose: () -> Unit,
    onSwitchToVoice: () -> Unit,
    onRetry: () -> Unit,
    onReset: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp, vertical = 24.dp)
            .testTag("chat-screen"),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TextButton(onClick = onClose) {
                Text(text = stringResource(id = R.string.chat_close_button))
            }
            Spacer(modifier = Modifier.weight(1f))
            TextButton(onClick = onReset) {
                Text(text = stringResource(id = R.string.chat_reset_button))
            }
            Spacer(modifier = Modifier.width(8.dp))
            Button(onClick = onSwitchToVoice) {
                Text(text = stringResource(id = R.string.chat_voice_button))
            }
        }
        Spacer(modifier = Modifier.height(8.dp))
        val composerEnabled = chatState is ChatConnectionState.Connected
        Box(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
        ) {
            when (chatState) {
                ChatConnectionState.Idle -> {
                    if (chatHistory.isEmpty()) {
                        ChatPlaceholder(text = stringResource(id = R.string.chat_empty_state))
                    } else {
                        ChatTranscriptList(messages = chatHistory)
                    }
                }
                ChatConnectionState.Connecting -> ChatPlaceholder(text = stringResource(id = R.string.chat_connecting))
                is ChatConnectionState.Failed -> ChatErrorState(message = chatState.reason, onRetry = onRetry)
                is ChatConnectionState.Connected -> ChatTranscriptList(messages = chatState.messages)
            }
        }
        Spacer(modifier = Modifier.height(12.dp))
        ChatComposer(
            value = inputValue,
            enabled = composerEnabled,
            onValueChange = onInputChange,
            onSend = onSend,
        )
    }
}

@Composable
private fun ChatTranscriptList(messages: List<ChatMessage>) {
    if (messages.isEmpty()) {
        ChatPlaceholder(text = stringResource(id = R.string.chat_empty_state))
        return
    }
    val expandedState = remember { mutableStateMapOf<String, Boolean>() }
    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .testTag("chat-list"),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(messages, key = { it.id }) { message ->
            val expanded = expandedState[message.id] == true
            ChatMessageBubble(
                message = message,
                isExpanded = expanded,
                onToggleExpand = {
                    expandedState[message.id] = !expanded
                },
            )
        }
    }
}

@Composable
private fun ChatMessageBubble(
    message: ChatMessage,
    isExpanded: Boolean,
    onToggleExpand: () -> Unit,
) {
    if (message.role == ChatMessageRole.TOOL) {
        ToolMessageBubble(message, isExpanded, onToggleExpand)
    } else {
        StandardChatBubble(message)
    }
}

@Composable
private fun StandardChatBubble(message: ChatMessage) {
    val isUser = message.role == ChatMessageRole.USER
    val background = if (isUser) {
        MaterialTheme.colorScheme.primary
    } else {
        MaterialTheme.colorScheme.surfaceVariant
    }
    val contentColor = if (isUser) {
        MaterialTheme.colorScheme.onPrimary
    } else {
        MaterialTheme.colorScheme.onSurfaceVariant
    }
    Box(
        modifier = Modifier.fillMaxWidth(),
        contentAlignment = if (isUser) Alignment.CenterEnd else Alignment.CenterStart,
    ) {
        Surface(
            color = background,
            contentColor = contentColor,
            shape = MaterialTheme.shapes.medium,
        ) {
            Text(
                text = message.text,
                modifier = Modifier.padding(12.dp),
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

@Composable
private fun ToolMessageBubble(
    message: ChatMessage,
    isExpanded: Boolean,
    onToggle: () -> Unit,
) {
    val payload = message.toolPayload.orEmpty()
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onToggle),
        color = MaterialTheme.colorScheme.secondaryContainer,
        contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
        shape = MaterialTheme.shapes.medium,
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                text = stringResource(
                    id = R.string.chat_tool_title,
                    message.messageType ?: stringResource(id = R.string.chat_tool_generic),
                ),
                style = MaterialTheme.typography.bodyMedium,
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = message.text,
                style = MaterialTheme.typography.bodySmall,
            )
            if (payload.isNotEmpty()) {
                Spacer(modifier = Modifier.height(8.dp))
                if (isExpanded) {
                    payload.entries.sortedBy { it.key }.forEach { (key, value) ->
                        Text(
                            text = "$key: ${value ?: "—"}",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                } else {
                    Text(
                        text = stringResource(id = R.string.chat_tool_expand_hint),
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        }
    }
}

@Composable
private fun ChatComposer(
    value: String,
    enabled: Boolean,
    onValueChange: (String) -> Unit,
    onSend: () -> Unit,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
    ) {
        OutlinedTextField(
            modifier = Modifier.weight(1f),
            value = value,
            onValueChange = onValueChange,
            enabled = enabled,
            placeholder = { Text(text = stringResource(id = R.string.chat_input_placeholder)) },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { onSend() }),
            singleLine = true,
        )
        Spacer(modifier = Modifier.width(12.dp))
        Button(
            onClick = onSend,
            enabled = enabled && value.isNotBlank(),
        ) {
            Text(text = stringResource(id = R.string.chat_send_button))
        }
    }
}

@Composable
private fun ChatPlaceholder(text: String) {
    Column(
        modifier = Modifier
            .fillMaxSize(),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun ChatErrorState(message: String, onRetry: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize(),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = message,
            style = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
            color = MaterialTheme.colorScheme.error,
        )
        Spacer(modifier = Modifier.height(12.dp))
        Button(onClick = onRetry) {
            Text(text = stringResource(id = R.string.chat_retry_button))
        }
    }
}

@Composable
private fun TranscriptBubble(
    message: TranscriptMessage,
    isExpanded: Boolean,
    onToggleExpand: () -> Unit,
) {
    val isTool = message.speaker == "tool" || message.toolPayload.orEmpty().isNotEmpty()
    if (isTool) {
        VoiceToolMessageBubble(
            message = message,
            isExpanded = isExpanded,
            onToggle = onToggleExpand,
        )
    } else {
        StandardTranscriptBubble(message)
    }
}

@Composable
private fun StandardTranscriptBubble(message: TranscriptMessage) {
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

@Composable
private fun VoiceToolMessageBubble(
    message: TranscriptMessage,
    isExpanded: Boolean,
    onToggle: () -> Unit,
) {
    val payload = message.toolPayload.orEmpty()
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 4.dp)
            .clickable(enabled = payload.isNotEmpty(), onClick = onToggle),
        color = MaterialTheme.colorScheme.secondaryContainer,
        contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
        shape = MaterialTheme.shapes.medium,
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                text = stringResource(
                    id = R.string.chat_tool_title,
                    message.messageType ?: stringResource(id = R.string.chat_tool_generic),
                ),
                style = MaterialTheme.typography.bodyMedium,
            )
            if (message.text.isNotBlank()) {
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = message.text,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            if (payload.isNotEmpty()) {
                Spacer(modifier = Modifier.height(8.dp))
                if (isExpanded) {
                    payload.entries.sortedBy { it.key }.forEach { (key, value) ->
                        Text(
                            text = "$key: ${value ?: "—"}",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                } else {
                    Text(
                        text = stringResource(id = R.string.chat_tool_expand_hint),
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        }
    }
}

private fun transcriptKey(message: TranscriptMessage, index: Int): String {
    val timestamp = message.timestampIso ?: ""
    val type = message.messageType ?: ""
    return "$timestamp|${message.speaker}|$type|$index|${message.text.hashCode()}"
}
