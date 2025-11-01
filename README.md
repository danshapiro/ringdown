# Ringdown

![Ringdown image](docs/assets/header.jpg)

Beep boop boop boop boop beep beep... ring, ring.

> *Hi Dan!*\
> Read the CoolTimes project spec.\
> *OK.*\
> Switch to Opus.\
> *OK.*\
> What technology platform should I use?\
> *Starts saying something about CoolMCP*\
> (interrupts) Find the readme for CoolMCP on Github and read it to me.\
> *Searching... Extracting... (Starts reading out loud)*\
> Actually, stop. Research on reddit to create a doc with the pros/cons of using CoolMCP. And email me a link to the github. By the way, set an appointment for tonight at eight to take out the trash.\
> (hangs up)


**Ringdown is your personal phone assistant.** You call it with a plain phone call, then it:
- Accesses Gsuite tools (makes appointments, sends emails, reads and writes documents)
- Does research, extracts full webpages, and - if you want - reads them to you
- Answers using your preferred LLM (any provider, fast or slow, witty or smart, you choose the prompt)
- Lets you switch models, prompts, tools, and more on the fly
- All this over your car's bluetooth or whatever other phone you have handy

> [!NOTE]
> This is not terribly secure: it depends on the obscurity of the number you're calling, and it uses caller ID to make sure it's you (which can be spoofed). Use at your own risk.

Ringdown is unabashedly single player. Everything revolves around your one Twilio number. If a second person tries to call while Ringdown is in a conversation, they are out of luck. (That said, if you want to give a friend or partner your ringdown number, it will use their caller ID to give them a custom experience! You might just need to wait on hold.)

(Why Ringdown? https://en.wikipedia.org/wiki/Ringdown)

The rest of this is, shockingly, AI generated.

## Make It Yours
1. Reserve or repurpose a Twilio number with ConversationRelay enabled.
2. Clone this repo, copy `config.example.yaml` to `config.yaml`, and fill in your greeting, default agent prompt, and any doc folders you want it to search.
3. Add your keys to `.env` (`OPENAI_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`). Optional integrations can wait.
4. Call yourself through `python -m utils.websocket_smoke --prompt "Quick test"` to hear the assistant’s first response.
5. Iterate on prompts until you like the tone, then deploy.

## Deploy Your Assistant (Cloud Run Quickstart)
New to Google Cloud? Follow these steps once, in order.

### Prerequisites
- Twilio phone number with ConversationRelay access.
- Google Cloud project with billing enabled.
- Python 3.11+, `git`, and the `gcloud` CLI installed locally.

### 1. Install and authenticate Google Cloud tools
```bash
curl -sSL https://sdk.cloud.google.com | bash
exec -l $SHELL
gcloud init
gcloud auth login
gcloud auth application-default login
```
Choose or create a project during `gcloud init` and set a default region (e.g., `us-central1`).

Enable the services Ringdown needs:
```bash
gcloud services enable run.googleapis.com secretmanager.googleapis.com \
  speech.googleapis.com texttospeech.googleapis.com
```

### 2. Clone and set up locally
```bash
git clone https://github.com/<your-handle>/ringdown.git
cd ringdown
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip uv
uv pip install -e .[dev]
cp config.example.yaml config.yaml
cp .env.example .env
```
Edit `config.yaml` to personalize prompts, fallback messages, and tool permissions. Update `.env` with at least your OpenAI and Twilio credentials.

### 3. Prepare secrets for deployment
- Store any local files (like a Gmail service-account key) under `secrets/`—that folder stays out of Git.
- Map each secret in `secret-manager.yaml` to either an environment variable or a file.
- Export the variables you filled in (`export OPENAI_API_KEY=...`, etc.) before deploying so the helper script can read them.

### 4. Deploy to Cloud Run
```bash
python cloudrun-deploy.py \
  --project-id <your-gcp-project> \
  --region us-central1 \
  --service ringdown \
  --secret-config secret-manager.yaml \
  --yes
```
The script builds the container, uploads your secrets, and creates or updates the Cloud Run service. Note the HTTPS URL when it finishes.

### 5. Point Twilio at your assistant
1. Open **Phone Numbers → Manage → Active numbers** in the Twilio Console and select your number.
2. Under **Voice & Fax**, set **A CALL COMES IN** to `Webhook`, paste the Cloud Run URL with `/twiml`, and choose `GET`.
3. Save. Place a real call to confirm Ringdown picks up.

### 6. Smoke-test
```bash
curl -sS "<your-cloud-run-url>/healthz"
python -m utils.websocket_smoke --url wss://<your-cloud-run-host>/ws --receive 3
```
Listen for the greeting and ensure the call summary appears in your logs.

### Managed A/V secrets (Daily Pipecat)
The Android client uses Daily's managed A/V pipeline. Capture these once and add them to your `.env` so `cloudrun-deploy.py` can sync them into Secret Manager:

1. Sign in to https://dashboard.daily.co with the account that owns the Ringdown pipeline.
2. Open https://pipecat.daily.co (same Daily credentials) and note the **Agent name** for the deployment. This value must match `mobile_managed_av.agent_name` in `config.yaml` (production uses `phone-danbot-agent`).
3. Generate a Pipecat Cloud token from **Developers → Tokens** and store it as `PIPECAT_API_KEY`.
4. (Optional) For handset audio-loop automation (ringdown-32), generate a shared secret and store it as `MANAGED_AV_CONTROL_TOKEN`. The backend only enables the control channel test harness when this token is present, and the live harness must supply it via the `X-Ringdown-Control-Token` header.
5. Run `cloudrun-deploy.py` and the helper will upload everything listed in `secret-manager.yaml`, wiring the environment variables into Cloud Run. After the first deploy, Android devices provision managed sessions without hitting the approval dialog.

## Android Managed A/V pipeline
- Configure realtime model, voice, and VAD defaults through the `defaults.realtime` block in `config.yaml`. Agent-specific overrides still live under `agents.<name>.realtime` and are merged automatically when provisioning sessions.
- Devices call `POST /v1/mobile/voice/session` to obtain a Daily room URL, access token, `pipelineSessionId`, and metadata describing the selected model, voice, and server VAD thresholds. The backend logs a structured `mobile_managed_session_started` event with the same identifiers for traceability across systems.
- The Pipecat pipeline posts transcripts to `POST /v1/mobile/managed-av/completions`. The backend streams the agent response through `stream_response`, logs a `mobile_managed_completion` entry (character counts and reset state included), and returns the assistant text plus optional hold/reset hints.
- When a session ends, Pipecat calls `DELETE /v1/mobile/managed-av/sessions/{session_id}`. The backend performs cleanup, closes the upstream session, and emits `mobile_managed_session_closed` with the pipeline handle for traceability.
- Smoke automation lives at `android/scripts/run-voice-smoke.sh`. The wrapper exports the correct `UV_PROJECT_ENVIRONMENT` and runs `uv run python -m app.mobile.smoke --device-id <device> --base-url <backend>`, validating session bootstrap, managed completions, and teardown end to end.
- The handset audio loop harness (`tests/live/handset_audio_loop.py`) enqueues deterministic PCM via the control channel, then uses `adb` to retrieve the handset-captured audio artifact for offline analysis.
- Automation helpers live in `tests/live/managed_session_helper.py` (fetch/create managed sessions via `GET /v1/mobile/managed-av/sessions/active`) and `tests/live/run_handset_harness.py` (ensures a session and runs the audio loop end to end for CI pipelines).
- Refresh the `.env` device pointer with `uv run python approve_new_phone.py sync-env`; it locates the newest enabled entry in `mobile_devices` and updates `LIVE_TEST_MOBILE_DEVICE_ID` for the harness.

### Pipecat pipeline notes
The Managed A/V pipeline is configured directly in the Pipecat Cloud console (or via the Pipecat CLI). When you tweak agent images, scaling limits, or credentials, update this README or your runbook with the new values—there is no local YAML source of truth. After any change, run `uv run python -m app.mobile.smoke --device-id <approved-device> --base-url <backend-url>` to verify managed sessions return useful responses.

## Technical Details
- Architecture: Twilio ConversationRelay streams audio to FastAPI over `/ws`, and the app routes messages through the `stream_response` pipeline (backed by your configured LLM provider), SQLite conversation memory, and any tools you enabled.
- Configuration: Override `RINGDOWN_CONFIG_PATH` to swap between "workday" and "weekend" personalities without editing files.
- Integrations: Add `TAVILY_API_KEY`, `GMAIL_IMPERSONATE_EMAIL`, and `GMAIL_SA_KEY_PATH` when you’re ready for search or email follow-ups. Without them, Ringdown politely skips those actions.
- Testing: `pytest`, `pytest -m integration`, and the WebSocket smoke helper keep regressions away before you push changes.
- Release workflow: bump `app.__version__`, update `pyproject.toml`, log changes in `CHANGELOG.md`, and tag (`v0.x.y`) when you publish a new build.

## License
Ringdown ships under the MIT License (see `LICENSE`).
