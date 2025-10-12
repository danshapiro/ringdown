# WebSocket Reconnection for Long-Running Calls

## Overview

Cloud Run has an absolute request timeout of 60 minutes that cannot be reset by activity. To support calls longer than 60 minutes, the application implements a graceful reconnection mechanism that maintains call continuity.

## How It Works

### Server-Side Behavior

1. **Connection Tracking**: Each WebSocket connection tracks its start time
2. **Timeout Detection**: At 55 minutes (3300 seconds), the server:
   - Sends a reconnection notification to the user
   - Closes the WebSocket with code 4000 and reason "Graceful reconnection required"
3. **State Preservation**: The call state (conversation history, agent configuration) is preserved in memory

### Client-Side Requirements

Twilio's ConversationRelay should handle reconnection automatically when it receives:
- WebSocket close code: 4000
- Close reason: "Graceful reconnection required"

The client should:
1. Immediately establish a new WebSocket connection to the same URL
2. Send a new `setup` message with the same CallSid
3. Resume the conversation where it left off

### User Experience

Users will hear:
> "I need to briefly reconnect our call to maintain quality. You'll hear a short beep and we'll continue right where we left off."

The reconnection should be nearly seamless with minimal interruption.

## Configuration

The reconnection timing is hardcoded to 55 minutes to provide a 5-minute buffer before Cloud Run's 60-minute timeout. This timing is not currently configurable.

## Monitoring

### Logs to Watch For

1. **Reconnection Initiated**:
   ```
   WARNING: Connection approaching 60-minute Cloud Run timeout (3301.0s), initiating graceful reconnection
   ```

2. **Connection Duration**:
   ```
   INFO: WebSocket connection duration: 00:55:01 (3301 seconds)
   ```

3. **Graceful Close**:
   ```
   INFO: Graceful reconnection initiated - approaching Cloud Run timeout
   ```

### Metrics

- Track the number of calls that reach 55 minutes
- Monitor successful reconnections
- Watch for any failed reconnections

## Testing

To test the reconnection mechanism:

1. Make a test call that lasts longer than 55 minutes
2. Monitor the logs for reconnection messages
3. Verify the call continues after reconnection

Example test command:
```bash
python tests/live_test_call.py --text "Read me a very long document" --silence-timeout 3400
```

## Limitations

1. **Absolute Timeout**: The 60-minute Cloud Run timeout is absolute and cannot be extended
2. **State Storage**: Currently, only in-memory state is preserved; if the instance restarts, state is lost
3. **Single Reconnection**: The current implementation only handles one reconnection per call

## Future Improvements

1. **Configurable Timing**: Make the reconnection timing configurable
2. **Multiple Reconnections**: Support multiple reconnections for calls longer than 2 hours
3. **Persistent State**: Store conversation state in a database for better reliability
4. **Client Notification**: Provide better client-side handling and user notifications

## Troubleshooting

### Call Drops at 60 Minutes Despite Reconnection

Check:
- Cloud Run service timeout configuration (should be 3600 seconds)
- Client-side reconnection handling
- Network connectivity during reconnection

### Reconnection Message Not Sent

Verify:
- Connection start time is being tracked correctly
- No errors in the message loop before 55 minutes
- WebSocket connection is still active

### State Lost After Reconnection

Ensure:
- CallSid is preserved in the client reconnection
- Agent state is correctly stored in `_CALL_AGENT_MAP`
- No instance restarts occurred during the call