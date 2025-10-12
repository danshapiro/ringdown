# Email tool configuration

The `SendEmail` tool integrates with Gmail via a delegated service account.  It
now fails gracefully when credentials are absent, returning a
`{"disabled": true}` payload to the caller instead of raising an exception.

## Prerequisites

1. **Enable Gmail API** (only when you plan to send email):
   ```bash
   gcloud services enable gmail.googleapis.com
   ```
2. **Create or reuse a service account** that has domain-wide delegation for the
   Gmail API scope `https://www.googleapis.com/auth/gmail.send`.
3. **Upload the JSON key** to Secret Manager and record the secret ID in
   `secret-manager.yaml`:
   ```bash
   gcloud secrets create ringdown-gmail-sa-key --data-file=/path/to/key.json
   ```
4. **Populate `secret-manager.yaml`** with the secret ID, optional mount path,
   and the env vars your deployment should expose.  The example file already
   contains a template entry.

`cloudrun-deploy.py` reads `secret-manager.yaml`, uploads new versions, and
binds the secrets to environment variables and mount paths automatically.

## Local development

Set the following environment variables in `.env` (or export them manually):

```
GMAIL_SA_KEY_PATH=/absolute/path/to/service-account.json
GMAIL_IMPERSONATE_EMAIL=your-mailbox@example.com
```

If either variable is omitted the tool responds with `integration_disabled` and
logs a helpful message.

## Per-agent recipient policies

Each agent inherits the global defaults defined in `config.example.yaml`.  You
can override recipients with regex patterns or explicit addresses:

```yaml
agents:
  ringdown-demo:
    email_greenlist_enforced: true
    email_greenlist:
      - "^[^@]+@example\\.com$"    # Allow any mailbox on example.com
      - "team@example.com"          # Explicit distribution list
```

Leave `email_greenlist_enforced` set to `false` if an agent should send to any
address.

## Troubleshooting

| Symptom | Resolution |
|---------|------------|
| `integration_disabled` response | Confirm `GMAIL_SA_KEY_PATH` and `GMAIL_IMPERSONATE_EMAIL` are set and the JSON key is accessible. |
| `HttpError 403` | The impersonated mailbox has not granted the service account `gmail.send` access.  Revisit domain-wide delegation in the Google Admin console. |
| Rate limit warnings | Ringdown enforces one outbound email every 10 seconds.  This is configurable via `_RATE_LIMIT_SECONDS` in `app/tools/email.py`. |
