# Configuration guide

This guide explains how to customise Ringdown for your organisation by editing
`config.yaml` and the supporting environment files.

## 1. Agents

Agents live under the `agents:` key.  Each agent inherits fields from the global
`defaults` block.

```yaml
agents:
  ringdown-demo:
    phone_numbers:
      - "+15555550100"
    continue_conversation: true
    docs_folder_greenlist:
      - "Ringdown-default"
    prompt: |-
      # Identity
      -   You are Ringdown, a configurable voice assistant.
      {ToolPrompts}
```

**Tips**

- Use E.164 format for phone numbers (`+1...`).  Ringdown validates that numbers
  are unique across agents.
- Keep prompts plain text — the WebSocket pipeline converts them directly to
  TTS.  Include `{ToolPrompts}` where tool-specific guidance should be inserted.
- Set `email_greenlist_enforced` to `true` if the agent must only email approved
  recipients.

## 2. Defaults

The `defaults` section configures shared behaviour:

```yaml
defaults:
  model: claude-sonnet-4-20250514
  backup_model: gemini/gemini-2.5-flash
  voice: en-US-Chirp3-HD-Aoede
  max_disconnect_seconds: 120
  max_tool_iterations: 6
```

- **Model and temperature** control the primary LLM.  A fallback model can be
  defined with `backup_model`.
- **Voice** accepts any name supported by your TTS provider.
- **Tool runner** messages appear while long-running tools execute.  You can add
  provider-specific phrases by editing `tool_runner.status_messages`.

## 3. Tools

To enable or disable tools globally, update `defaults.tools`.  Agents may extend
or restrict the set by specifying a `tools` list of their own.

Available tools are registered under `app/tools/`.  Each module contains
configuration comments describing required environment variables.

## 4. Document access

If you enable Google Docs tools, restrict the allowed folders using
`docs_folder_greenlist` either globally (`docs_folder_greenlist_defaults`) or per
agent.  Values can be plain strings or regular expressions.

## 5. Environment variables

Ringdown loads environment variables via `pydantic-settings`.

- Copy `.env.example` to `.env` and fill in mandatory secrets.
- Use `.env` for local development only — production deployments should inject
  secrets via Secret Manager.
- Optional features (Tavily, Gmail, Calendar) can be omitted; the tools detect
  missing credentials and return structured errors.

## 6. Secret Manager mapping

`secret-manager.example.yaml` provides a declarative mapping between secrets and
runtime usage.  Each entry supports:

```yaml
- secret_id: ringdown-openai-key
  value_from_env: OPENAI_API_KEY
  env_var: OPENAI_API_KEY
```

or

```yaml
- secret_id: ringdown-gmail-sa-key
  source: ./secrets/gmail-sa.json
  env_var: GMAIL_SA_KEY_PATH
  mount_path: /var/secrets/gmail-sa-key.json
```

The deployment script ensures secrets exist, uploads the payload, and grants the
Cloud Run service account access.

## 7. Testing configuration changes

1. Run `pytest` to execute the unit test suite.
2. Use `pytest -m integration` if you modified external-tool behaviour.
3. For Twilio-specific changes, the scripts under `tests/live_test_*.py` provide
   end-to-end calls; mark them with the `live` marker to keep default runs fast.

## 8. Regenerating audio prompts

The `sounds/` directory contains example mp3 files used for thinking and
completion tones.  To regenerate them locally run:

```bash
python generate_audio_file.py --text "Processing" --output sounds/thinking.mp3
```

Ensure you have permission to redistribute any custom audio before committing it
(or prefer generated assets stored outside of git history).

## 9. Utility scripts for operations

Ringdown ships with a few helper utilities.  Each assumes credentials are
configured via `.env` (for local runs) or Google Cloud's `gcloud auth` tooling
when interacting with managed services:

### `generate_audio_file.py`

*Purpose* – Create short mp3 clips using OpenAI's text-to-speech API.  Use this
for placeholder greetings or "thinking" tones.

*Prerequisites*

* `OPENAI_API_KEY` in your environment or `.env` file.
* Network access to the OpenAI API.

*Usage*

```bash
python generate_audio_file.py --text "Thanks for calling Ringdown" --output sounds/ringdown-welcome.mp3
```

Provide `--voice` / `--model` flags to target alternate TTS voices.  The script
prints progress as the clip is rendered and writes the mp3 to disk.

### `log_love.py`

*Purpose* – Tail application logs with optional colourised output for quick
local debugging.  This complements structured logging in production.

*Prerequisites*

* No special credentials; uses your local filesystem.

*Usage*

```bash
python log_love.py --follow logs/app.log
```

Pass `--stdout` to stream application output when running under Docker.

### `utils/mp3_uploader.py`

*Purpose* – Upload generated audio to Google Cloud Storage and expose an HTTPS
URL suitable for Twilio prompts.

*Prerequisites*

* `gcloud auth login` and `gcloud auth application-default login` executed so the
  active account can create buckets and objects.
* `gcloud config set project <your-project>` to establish the default project.

*Usage*

```python
from pathlib import Path
from twilio.rest import Client
from utils.mp3_uploader import upload_mp3_to_twilio

client = Client(account_sid, auth_token)
public_url = upload_mp3_to_twilio(client, Path("sounds/ringdown-welcome.mp3"))
print(public_url)
```

The helper lazily creates a bucket named `<project>-test-assets` and ensures the
uploaded blob is publicly accessible over HTTPS.

## 10. Production audio hosting guidance

For production deployments, store audio files outside of the repository and
serve them via a TLS-enabled CDN:

1. Upload mp3 assets to a dedicated Cloud Storage bucket (or equivalent).
2. Configure cache headers (`Cache-Control: public, max-age=86400`) so repeat
   callers do not redownload the clip for every call.
3. Grant the bucket read-only access to the public (or front it with Cloud CDN)
   and ensure the URL served to Twilio is HTTPS.
4. Set `RINGDOWN_TWIML_URL` when running `tests/live_test_call.py` so chained
   test flows know which `/twiml` endpoint to connect to.

The `utils/mp3_uploader.py` helper provides a quick path for development; for
production, automate uploads via CI to keep assets versioned and auditable.
