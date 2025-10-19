package com.ringdown.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle

@Composable
fun RingdownAppRoot(
    viewModel: MainViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    Surface(
        modifier = Modifier.fillMaxSize(),
        color = MaterialTheme.colorScheme.background
    ) {
        when (val current = state) {
            AppViewState.Loading -> LoadingScreen()
            AppViewState.Idle -> IdleScreen(
                onReconnect = viewModel::onReconnectRequested,
                onOpenChat = { /* TODO: upcoming phase */ }
            )
            is AppViewState.PendingApproval -> PendingApprovalScreen(
                attempts = current.attempts,
                deviceId = current.deviceId,
                onCheckAgain = viewModel::onCheckAgain
            )
            is AppViewState.Error -> ErrorScreen(
                message = current.message,
                onRetry = viewModel::onCheckAgain
            )
        }
    }
}

@Composable
private fun LoadingScreen() {
    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center
    ) {
        CircularProgressIndicator()
    }
}

@Composable
private fun IdleScreen(
    onReconnect: () -> Unit,
    onOpenChat: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Button(
            modifier = Modifier
                .fillMaxWidth()
                .padding(bottom = 16.dp),
            onClick = onReconnect
        ) {
            Text(text = "Reconnect")
        }
        Button(
            modifier = Modifier.fillMaxWidth(),
            onClick = onOpenChat
        ) {
            Text(text = "Open Chat")
        }
    }
}

@Composable
private fun PendingApprovalScreen(
    attempts: Int,
    deviceId: String,
    onCheckAgain: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(
            text = "Device pending approval.",
            style = MaterialTheme.typography.headlineSmall,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(bottom = 16.dp)
        )
        Text(
            text = "Device ID: ${deviceId.ifBlank { "unknown" }}",
            textAlign = TextAlign.Center,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier
                .fillMaxWidth()
                .padding(bottom = 16.dp)
        )
        Text(
            text = "Attempts: $attempts",
            style = MaterialTheme.typography.labelLarge,
            modifier = Modifier.padding(bottom = 24.dp)
        )
        Button(
            modifier = Modifier.fillMaxWidth(),
            onClick = onCheckAgain
        ) {
            Text(text = "Check again")
        }
    }
}

@Composable
private fun ErrorScreen(
    message: String,
    onRetry: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(
            text = message,
            textAlign = TextAlign.Center,
            style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.padding(bottom = 24.dp)
        )
        Button(onClick = onRetry) {
            Text(text = "Retry")
        }
    }
}
