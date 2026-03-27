# Main And Android Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use trycycle-executing to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land a clean backend-only `main` that matches `origin/main`, preserve the useful non-Android work from `android-client`, and rebuild a canonical Android branch on top of that cleaned `main` with its full backend support and green test suites.

**Architecture:** Do not rebase `android-client` wholesale. Build the new steady state in two passes: first subtract Android/mobile-only surface from the current local `main` tip and selectively reapply backend-only improvements from `android-client`; then branch from that cleaned `main` and restore only the Android/mobile surface plus the Android-specific versions of shared files. Preserve the current dirty `android-client` worktree and old remote tips as archives; the canonical clean Android line becomes a new local branch while `origin/android-client` is updated with `--force-with-lease`.

**Tech Stack:** Git worktrees and archive refs, FastAPI, Python 3.12, `uv`, `pytest`, `ruff`, Kotlin/Gradle Android tests, `adb`.

---

## Strategy And Boundary Decisions

1. `main` becomes backend-only. That means no `android/` tree, no `/v1/mobile*` API surface, no mobile-device config contract, no Android approval helper, no Android smoke docs, and no Android-only tests.
2. Android-supporting backend code belongs with the Android branch. Files such as `app/api/mobile.py`, `app/api/mobile_text.py`, `app/mobile/*`, Android smoke tests, and device-approval tooling are part of the Android branch even when they live outside `android/`.
3. The current dirty local `android-client` worktree must not be rewritten in place. Keep it intact as preserved WIP. The clean reconstructed line is `android-client-clean` locally, and that branch is what gets pushed to `origin/android-client`.
4. Keep the cleaned-main dependency baseline on both branches. Do not restore the stale `android-client` dependency set: its head no longer uses `aiortc`/`numpy`, and its `pyproject.toml` regresses LiteLLM. The rebuilt Android branch should inherit `main`'s newer LiteLLM stack and only add code, not dead dependencies.
5. No compatibility fallback on `main`. The API contract changes intentionally: after cutover, `main` does not serve mobile routes. The Android branch owns that contract outright.

## Steady-State Invariants

- `main` and `origin/main` resolve to the same tested commit.
- Cleaned `main` contains the useful backend-only improvements from `android-client`: widened Google Docs reads, the Todo tool, structured LLM log markers plus log formatter, and rate-limit backoff in `app/chat.py`.
- Cleaned `main` contains none of the Android/mobile-only tree or config surface.
- `android-client-clean` is based on the cleaned `main` commit and reintroduces the Android app, Android/mobile backend files, Android docs, and Android tests.
- Old tips are preserved under archive refs before any remote branch rewrite.

## File Map

### Cleaned `main` keeps or adds

- `.gitignore`
- `AGENTS.md`
- `README.md`
- `app/chat.py`
- `app/tools/google_docs.py`
- `app/tools/todo.py`
- `app/tools/change_llm.py`
- `scripts/reformat_litellm_log.py`
- `config.example.yaml`
- `docs/configuration_guide.md`
- `pyproject.toml`
- `uv.lock`
- `tests/fixtures/config.test.yaml`
- `tests/test_app.py`
- `tests/test_change_llm_tool.py`
- `tests/test_error_cases.py`
- `tests/test_google_docs_tool.py`
- `tests/test_reformat_litellm_log.py`
- `tests/test_todo_tool.py`
- `tests/live_test_all_functions.py`

### Cleaned `main` removes

- `app/api/mobile.py`
- `app/api/mobile_text.py`
- `app/mobile/__init__.py`
- `app/mobile/config_store.py`
- `app/mobile/smoke.py`
- `app/mobile/text_session_store.py`
- `app/main.py` mobile router wiring
- `app/api/__init__.py` mobile exports
- `app/settings.py` mobile getters/env fields
- `app/config_schema.py` mobile device and mobile text schema
- `authorize_new_phone.py`
- `docs/voice-smoke-ci.md`
- `tests/test_mobile_registration.py`
- `tests/test_mobile_voice_session.py`
- `tests/test_mobile_text_session.py`
- `tests/test_mobile_text_handshake.py`
- `tests/test_mobile_smoke.py`
- `tests/test_manual_voice_harness.py`
- `tests/test_run_local_voice_smoke.py`
- `tests/test_android_install_script.py`
- `todo-android-spec/todo-android-spec.txt`
- `todo-android-spec/mockup-1-pending-approval.html`
- `todo-android-spec/mockup-2-idle-main.html`
- `todo-android-spec/mockup-3-voice-active.html`
- `todo-android-spec/mockup-4-voice-reconnecting.html`
- `todo-android-spec/mockup-5-chat-session.html`
- `todo-android-spec/mockup-6-chat-tool-expanded.html`
- `todo-android-spec/mockup-7-permission-denied.html`
- `todo-android-spec/mockup-8-background-notification.html`

### Rebuilt Android branch restores or owns

- `android/`
- `app/api/mobile.py`
- `app/api/mobile_text.py`
- `app/mobile/__init__.py`
- `app/mobile/config_store.py`
- `app/mobile/smoke.py`
- `app/mobile/text_session_store.py`
- `approve_new_phone.py`
- `AGENTS.md` Android sections
- `README.md` Android sections
- `app/main.py`
- `app/api/__init__.py`
- `app/settings.py`
- `app/config_schema.py`
- `config.example.yaml`
- `.env.example`
- `docs/voice-smoke-ci.md`
- `tests/test_approve_new_phone.py`
- `tests/test_mobile_registration.py`
- `tests/test_mobile_text_session.py`
- `tests/test_mobile_text_handshake.py`
- `tests/test_mobile_smoke.py`
- `tests/test_manual_voice_harness.py`
- `tests/test_run_local_voice_smoke.py`
- `tests/test_android_install_script.py`
- `todo-android-local-codex.txt`
- `todo-android-spec/`

## Cutover Rules

- Archive the pre-split tips before pushing anything:
  - `archive/main-pre-split-20260326`
  - `archive/android-client-pre-split-20260326`
- Do not rename or move the dirty local `android-client` branch.
- Fast-forward the clean local `main` worktree; rewrite only the remote `android-client` branch, and only with `--force-with-lease` pinned to the observed old tip `702abb1`.
- For Android smoke against local code, run the backend locally and expose it to the device with `adb reverse`; do not rely on the old deployed branch.

### Task 1: Preserve Current Graph And Prove The Split Target

**Files:**
- Modify: none; this task only creates archive refs and records git state
- Test: git graph and worktree-state commands

- [ ] **Step 1: Identify the current unsafe state**

Run:

```bash
git branch -vv
git worktree list --porcelain
git stash list
git status --short
git rev-list --left-right --count origin/main...main
git rev-list --left-right --count origin/android-client...android-client
```

Expected: `main` is ahead of `origin/main`, `android-client` is the long-lived divergent line, and there is a dirty checked-out `android-client` worktree that must stay untouched.

- [ ] **Step 2: Create archive refs before any rewrite**

Run:

```bash
git branch archive/main-pre-split-20260326 80f2f5d
git branch archive/android-client-pre-split-20260326 702abb1
git push origin 6dd3051:refs/heads/archive/main-pre-split-20260326
git push origin 702abb1:refs/heads/archive/android-client-pre-split-20260326
```

Expected: both local archive branches exist and both remote archive branches are created successfully.

- [ ] **Step 3: Verify the archives point at the intended pre-split tips**

Run:

```bash
git rev-parse --short archive/main-pre-split-20260326
git rev-parse --short archive/android-client-pre-split-20260326
git ls-remote --heads origin archive/main-pre-split-20260326 archive/android-client-pre-split-20260326
```

Expected: the local and remote archive refs resolve to `80f2f5d` and `702abb1`.

- [ ] **Step 4: Commit**

No tracked-file commit is needed here. The archives are the safety boundary for the rest of the cutover.

### Task 2: Remove Android And Mobile Surface From `main`

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `app/main.py`
- Modify: `app/api/__init__.py`
- Modify: `app/settings.py`
- Modify: `app/config_schema.py`
- Modify: `config.example.yaml`
- Modify: `tests/test_app.py`
- Modify: `tests/test_config_schema.py`
- Delete: `app/api/mobile.py`
- Delete: `app/api/mobile_text.py`
- Delete: `app/mobile/__init__.py`
- Delete: `app/mobile/config_store.py`
- Delete: `app/mobile/smoke.py`
- Delete: `app/mobile/text_session_store.py`
- Delete: `authorize_new_phone.py`
- Delete: `docs/voice-smoke-ci.md`
- Delete: `tests/test_mobile_registration.py`
- Delete: `tests/test_mobile_voice_session.py`
- Delete: `tests/test_mobile_text_session.py`
- Delete: `tests/test_mobile_text_handshake.py`
- Delete: `tests/test_mobile_smoke.py`
- Delete: `tests/test_manual_voice_harness.py`
- Delete: `tests/test_run_local_voice_smoke.py`
- Delete: `tests/test_android_install_script.py`
- Delete: `todo-android-spec/todo-android-spec.txt`
- Delete: `todo-android-spec/mockup-1-pending-approval.html`
- Delete: `todo-android-spec/mockup-2-idle-main.html`
- Delete: `todo-android-spec/mockup-3-voice-active.html`
- Delete: `todo-android-spec/mockup-4-voice-reconnecting.html`
- Delete: `todo-android-spec/mockup-5-chat-session.html`
- Delete: `todo-android-spec/mockup-6-chat-tool-expanded.html`
- Delete: `todo-android-spec/mockup-7-permission-denied.html`
- Delete: `todo-android-spec/mockup-8-background-notification.html`
- Test: `tests/test_app.py`
- Test: `tests/test_config_schema.py`

- [ ] **Step 1: Prove the current branch still exposes Android/mobile surface**

Run:

```bash
git ls-tree -r --name-only HEAD | rg '^(app/api/mobile.py|app/mobile/|tests/test_mobile_|docs/voice-smoke-ci.md|todo-android-spec/)'
rg -n 'include_router\\(mobile|mobile_devices|mobile_text|LIVE_TEST_MOBILE_DEVICE_ID' app/main.py app/api/__init__.py app/settings.py app/config_schema.py config.example.yaml
```

Expected: matches are present. This is the contract that must disappear from cleaned `main`.

- [ ] **Step 2: Make the invariant executable**

Update `tests/test_app.py` and `tests/test_config_schema.py` so cleaned `main` is checked by code:
- `tests/test_app.py` should assert the FastAPI route table does not include any path beginning with `/v1/mobile`.
- `tests/test_config_schema.py` should assert `ConfigModel` no longer declares mobile-specific schema fields while still validating the repository `config.yaml` through `extra="allow"` so user-local config is tolerated.

- [ ] **Step 3: Remove the Android/mobile surface from the branch**

Implementation notes:
- Delete the Android/mobile-only files listed above.
- Remove mobile router imports from `app/main.py` and `app/api/__init__.py`.
- Remove mobile env/settings helpers from `app/settings.py`.
- Remove `MobileDeviceConfig` and any mobile validation from `app/config_schema.py`.
- Remove `mobile_devices` and other Android-only config/examples from `config.example.yaml`.
- Trim `AGENTS.md` and `README.md` so `main` documents backend-only workflows.

- [ ] **Step 4: Run the targeted checks**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests/test_app.py tests/test_config_schema.py -v
```

Expected: PASS, and the route/config tests prove `main` no longer owns Android/mobile contracts.

- [ ] **Step 5: Run the broader backend regression slice**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests/test_app.py tests/test_config_schema.py tests/test_error_cases.py tests/test_change_llm_tool.py tests/test_google_docs_tool.py -v
uv run ruff check
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md README.md app/main.py app/api/__init__.py app/settings.py app/config_schema.py config.example.yaml tests/test_app.py tests/test_config_schema.py
git add -u app/api app/mobile authorize_new_phone.py docs tests todo-android-spec
git commit -m "refactor: remove android surface from main"
```

### Task 3: Port Backend-Only Improvements Onto Cleaned `main`

**Files:**
- Modify: `README.md`
- Modify: `app/chat.py`
- Modify: `app/tools/google_docs.py`
- Modify: `config.example.yaml`
- Modify: `tests/fixtures/config.test.yaml`
- Modify: `tests/test_google_docs_tool.py`
- Modify: `tests/test_error_cases.py`
- Create: `app/tools/todo.py`
- Create: `scripts/reformat_litellm_log.py`
- Create: `tests/test_todo_tool.py`
- Create: `tests/test_reformat_litellm_log.py`
- Test: `tests/test_google_docs_tool.py`
- Test: `tests/test_todo_tool.py`
- Test: `tests/test_reformat_litellm_log.py`
- Test: `tests/test_error_cases.py`

- [ ] **Step 1: Bring over the failing tests before the implementation**

Copy or re-create the backend-only tests from `android-client`:
- `tests/test_todo_tool.py`
- `tests/test_reformat_litellm_log.py`
- the widened-read assertions in `tests/test_google_docs_tool.py`
- a new `tests/test_error_cases.py` case that proves `stream_response` retries `openai.RateLimitError`, respects `retry-after` when present, and only surfaces a `RateLimitError:` token after retries are exhausted

- [ ] **Step 2: Run the targeted tests to verify they are red**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests/test_google_docs_tool.py tests/test_todo_tool.py tests/test_reformat_litellm_log.py tests/test_error_cases.py -v
```

Expected: FAIL because the Todo tool, log formatter, widened Google Docs read behavior, and rate-limit retry logic are not fully present on cleaned `main`.

- [ ] **Step 3: Implement the minimal backend-only transplant**

Implementation notes:
- Reapply `c11bc17` into `app/tools/google_docs.py` and `tests/test_google_docs_tool.py`.
- Reapply `84b451d` into `app/tools/todo.py`, `config.example.yaml`, `tests/fixtures/config.test.yaml`, and `tests/test_todo_tool.py`.
- Reapply `d8820d7` into `app/chat.py`, `scripts/reformat_litellm_log.py`, `README.md`, and `tests/test_reformat_litellm_log.py`, but do **not** resurrect deleted artifact files.
- Reapply `702abb1` into `app/chat.py`, adapted on top of the newer local-`main` model-routing code so the retry/backoff logic coexists with `openai/gpt-5.2` routing.

- [ ] **Step 4: Run the targeted tests again**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests/test_google_docs_tool.py tests/test_todo_tool.py tests/test_reformat_litellm_log.py tests/test_error_cases.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the full cleaned-main regression suite**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests
uv run ruff check
```

Expected: PASS. If any surviving test still assumes Android/mobile surface on `main`, fix that test by updating it to the new backend-only contract, not by restoring the removed feature.

- [ ] **Step 6: Commit**

```bash
git add README.md app/chat.py app/tools/google_docs.py app/tools/todo.py scripts/reformat_litellm_log.py config.example.yaml tests/fixtures/config.test.yaml tests/test_google_docs_tool.py tests/test_todo_tool.py tests/test_reformat_litellm_log.py tests/test_error_cases.py
git commit -m "feat: split main from android and keep backend improvements"
```

- [ ] **Step 7: Fast-forward local `main` and publish it**

Run:

```bash
git -C /mnt/d/Users/Dan/GoogleDrivePersonal/code/ringdown/.worktrees/main-deploy merge --ff-only trycycle-main-android-split
git -C /mnt/d/Users/Dan/GoogleDrivePersonal/code/ringdown/.worktrees/main-deploy push origin main
git rev-list --left-right --count origin/main...main
```

Expected: the merge is fast-forward only, the push succeeds, and the final divergence count is `0	0`.

### Task 4: Rebuild The Android Branch On Top Of The New `main`

**Files:**
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `app/main.py`
- Modify: `app/api/__init__.py`
- Modify: `app/settings.py`
- Modify: `app/config_schema.py`
- Modify: `config.example.yaml`
- Modify: `tests/fixtures/config.test.yaml`
- Modify: `tests/live_test_all_functions.py`
- Create: `android/`
- Create: `app/api/mobile.py`
- Create: `app/api/mobile_text.py`
- Create: `app/mobile/__init__.py`
- Create: `app/mobile/config_store.py`
- Create: `app/mobile/smoke.py`
- Create: `app/mobile/text_session_store.py`
- Create: `approve_new_phone.py`
- Create: `docs/voice-smoke-ci.md`
- Create: `tests/test_approve_new_phone.py`
- Create: `tests/test_mobile_registration.py`
- Create: `tests/test_mobile_text_session.py`
- Create: `tests/test_mobile_text_handshake.py`
- Create: `tests/test_mobile_smoke.py`
- Create: `tests/test_manual_voice_harness.py`
- Create: `tests/test_run_local_voice_smoke.py`
- Create: `tests/test_android_install_script.py`
- Create: `todo-android-local-codex.txt`
- Create: `todo-android-spec/`
- Test: `tests/test_mobile_registration.py`
- Test: `tests/test_mobile_text_session.py`
- Test: `tests/test_mobile_text_handshake.py`

- [ ] **Step 1: Start a clean Android reconstruction branch from the published `main`**

Run:

```bash
git switch -c android-client-clean main
git rev-parse --short HEAD
git ls-tree -r --name-only HEAD | rg '^android/' || true
```

Expected: the branch starts at the cleaned `main` commit and currently has no Android tree.

- [ ] **Step 2: Restore the Android/mobile-owned files from the old Android line**

Run:

```bash
git restore --source=android-client -- android
git restore --source=android-client -- app/api/mobile.py app/api/mobile_text.py app/mobile approve_new_phone.py docs/voice-smoke-ci.md tests/test_approve_new_phone.py tests/test_mobile_registration.py tests/test_mobile_text_session.py tests/test_mobile_text_handshake.py tests/test_mobile_smoke.py tests/test_manual_voice_harness.py tests/test_run_local_voice_smoke.py tests/test_android_install_script.py todo-android-local-codex.txt todo-android-spec
git restore --source=android-client -- .env.example .gitignore AGENTS.md README.md app/main.py app/api/__init__.py app/settings.py app/config_schema.py config.example.yaml tests/fixtures/config.test.yaml tests/live_test_all_functions.py
```

Expected: the Android/mobile files come back, but the branch still inherits cleaned-main backend improvements.

- [ ] **Step 3: Reconcile the shared files deliberately instead of trusting wholesale restore**

Implementation notes:
- Keep `main`'s `pyproject.toml` and `uv.lock`; do not restore the stale Android-branch versions.
- Merge `AGENTS.md`, `README.md`, `config.example.yaml`, `tests/fixtures/config.test.yaml`, `app/main.py`, `app/api/__init__.py`, `app/settings.py`, and `app/config_schema.py` so they contain both the cleaned-main backend improvements and the Android/mobile additions.
- Confirm `app/chat.py`, `app/tools/google_docs.py`, `app/tools/todo.py`, and `scripts/reformat_litellm_log.py` stay on the cleaned-main versions unless the Android head truly has newer compatible code.

- [ ] **Step 4: Run the targeted Android/mobile Python tests**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests/test_approve_new_phone.py tests/test_mobile_registration.py tests/test_mobile_text_session.py tests/test_mobile_text_handshake.py tests/test_mobile_smoke.py tests/test_manual_voice_harness.py tests/test_run_local_voice_smoke.py tests/test_android_install_script.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the broader Python regression suite on the rebuilt branch**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests
uv run ruff check
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .env.example .gitignore AGENTS.md README.md app/main.py app/api/__init__.py app/settings.py app/config_schema.py config.example.yaml tests/fixtures/config.test.yaml tests/live_test_all_functions.py
git add android app/api/mobile.py app/api/mobile_text.py app/mobile approve_new_phone.py docs/voice-smoke-ci.md tests/test_approve_new_phone.py tests/test_mobile_registration.py tests/test_mobile_text_session.py tests/test_mobile_text_handshake.py tests/test_mobile_smoke.py tests/test_manual_voice_harness.py tests/test_run_local_voice_smoke.py tests/test_android_install_script.py todo-android-local-codex.txt todo-android-spec
git commit -m "feat: rebuild android client branch on top of cleaned main"
```

### Task 5: Verify Android End To End And Publish The Canonical Branch

**Files:**
- Modify: whichever files require final test-driven fixups from Task 4
- Test: `tests`
- Test: `android` unit and connected suites

- [ ] **Step 1: Identify any remaining failing Android checks**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests -q
bash android/scripts/gradle.sh :app:testDebugUnitTest
```

Expected: either everything is already green or the failing checks tell you exactly which final Android/shared-file mismatches remain.

- [ ] **Step 2: Fix the last Android/shared regressions**

Implementation notes:
- Prefer fixing the code over weakening tests.
- If a failure is caused by the main/Android boundary itself, adjust the branch-owned shared file, not the cleaned-main contract.

- [ ] **Step 3: Run the full Android verification matrix**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests
uv run ruff check
bash android/scripts/gradle.sh :app:testDebugUnitTest
bash android/scripts/gradle.sh :app:connectedVoiceMvpAndroidTest
adb reverse tcp:8000 tcp:8000
UV_PROJECT_ENVIRONMENT=.venv-wsl uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In a second shell, run:

```bash
bash android/scripts/run-voice-smoke.sh --backend http://127.0.0.1:8000
```

Expected: every automated suite passes, and the voice smoke reaches the locally running rebuilt backend through `adb reverse`.

- [ ] **Step 4: Commit any final test-driven fixes**

```bash
git add -A
git commit -m "fix: finish android branch rebuild verification"
```

- [ ] **Step 5: Publish the canonical Android line without touching the dirty local `android-client` branch**

Run:

```bash
git push origin android-client-clean
git push origin android-client-clean:android-client --force-with-lease=refs/heads/android-client:702abb1
git ls-remote --heads origin android-client android-client-clean archive/android-client-pre-split-20260326
```

Expected: `origin/android-client` now points at the rebuilt commit, `origin/android-client-clean` exists for clarity, and the archive branch still points at `702abb1`.

- [ ] **Step 6: Verify the final branch topology**

Run:

```bash
git rev-list --left-right --count origin/main...main
git rev-parse --short origin/android-client
git merge-base --is-ancestor origin/main origin/android-client
```

Expected: `main` and `origin/main` are aligned, and the rebuilt Android branch is based on the published cleaned `main`.
