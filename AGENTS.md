## Coding Standards

## DON'T DO THIS
Unless the user specifically requests it, **NEVER** perform:
- Git add or commit
- Destructive git operations
- Render tests ineffective (make them less rigorous, skip them, delete them, etc)
Instead of performing these actions, only recommend them, and have the user either confirm it or do it themselves.

### General Guidelines
- **write ad-hoc scripts in Python**
- **Use bash** unless there's a specific reason to use powershell
- **Mind the venv**: In bash, use `.venv-wsl`; on PowerShell or CMD, use `.venv`. Both live in this repo—activate the one that matches your shell.
- **Always run tests after changes**: `uv run pytest`
- **Follow coding standards**: Write code that will pass ruff and mypy

### Python Standards
- **Error Handling**: Fail fast, no silent failures, log all errors
- **Configuration**: No fallback values - fail cleanly if something is missing
- **No Unicode in Scripts**: NEVER use unicode characters (✓ emoji, etc.) in Python scripts; they FAIL in windows
- **Path Handling**: Always use `pathlib.Path()` for cross-platform paths

#### Writing and maintaining tests
- **New functionality**: Add and then run tests that cover the primary behavior, error paths, and edge cases. 
- **When modifying existing functionality**: Run tests. If they fail, read them to decide whether to update the tests to match the new contract OR fix the code. Add new tests if gaps exist.
- **Avoid brittle config assumptions**: Do not hardcode values in tests that live in config files (e.g., `config.yaml`) or environment-specific settings. They change often. Use fixtures/helpers to load config or mock via settings/env overrides instead of relying on constants.

## Setup and Run Commands

### Backend (Python)
- **IMPORTANT**: Always modify `pyproject.toml` and use `uv sync` to manage the environment, NOT `uv pip install`
  - Use `uv run`
- Before running `uv`, set `UV_PROJECT_ENVIRONMENT` so it picks the right virtualenv:
  - PowerShell/CMD: `$env:UV_PROJECT_ENVIRONMENT = ".venv"` (add to your profile for persistence).
  - WSL/Bash: `export UV_PROJECT_ENVIRONMENT=.venv-wsl` (add to `.bashrc`/`.zshrc`).

### Android Client & Voice Tests
- **Python test suite:** run `uv run pytest tests` (Windows: avoid the repo root to skip `android/.gradle-cache` permission errors). Alternatively call `.venv\Scripts\python.exe -m pytest tests`.
- **API registration tests** (`tests/test_mobile_registration.py`) require `OPENAI_API_KEY` in the environment; the suite fails fast if it’s missing.
- **Voice smoke test:** once a device/emulator shows up in `adb devices`, run `bash android/scripts/run-voice-smoke.sh --backend $BACKEND_URL`. The script wraps `connectedDebugAndroidTest` and expects the same `ANDROID_SERIAL` that `mobile-mcp` uses.
- **Instrumentation toggles:** to force the fake transport during tests set `DebugFeatureFlags.overrideVoiceTransportStub(true)`—handled automatically by the existing `VoiceMvpSuite`; no manual changes needed when running the suite.
- **Mobile MCP deploy:** `bash android/scripts/install.sh --device $ANDROID_SERIAL` deploys the latest debug APK; ensure `uv run pytest tests` is clean before pushing to devices.

### Deployment (Cloud Run)
- From the repo root, activate the matching virtual environment (`.venv` on PowerShell/CMD, `.venv-wsl` on bash).
- Make sure `gcloud auth application-default login` is already configured for the `danbot-twilio` project.
- Run `python cloudrun-deploy.py` and wait for it to finish; it builds the image, updates secrets, and redeploys the service.
- The deploy script can exceed the default 2‑minute timeout in the Codex harness—if it gets killed mid-run, rerun it from a local shell outside the harness or split the workflow into smaller steps.

## Helpful tools
- `gh` for github work including CI status
- `gcloud` for google cloud work including checking logs



