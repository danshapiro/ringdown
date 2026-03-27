# Main And Android Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use trycycle-executing to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a backend-only `main` that matches `origin/main`, preserve the useful backend work that currently lives only on `android-client`, and publish a rebuilt `android-client` branch that contains the full Android app plus every Android-owned backend/shared-file delta, with all required Python and Android test lanes green.

**Architecture:** Treat the split as a file-ownership problem, not a wholesale branch rebase. Start from the current local `main` tip, remove the Android/mobile contract from that line, then port only the backend-only improvements from `android-client`. After `main` is green and published, create `android-client-clean` from that cleaned `main` and restore every Android-owned file plus the Android-specific versions of shared files that changed outside `android/`.

**Tech Stack:** Git worktrees and archive refs, FastAPI, Python 3.12, `uv`, `pytest`, `ruff`, Kotlin/Gradle Android tests, `adb`.

---

## Strategy And Boundary Decisions

1. Use dynamic refs during execution. The currently observed state is `main=80f2f5d`, `origin/main=6dd3051`, `android-client=702abb1`, `origin/android-client=702abb1`, but every archive/push step must resolve the live ref first instead of trusting hardcoded hashes.
2. `main` becomes backend-only. After the split, `main` must not serve `/v1/mobile*`, must not require Android/mobile config, and must not contain Android-only helper scripts, smoke harness docs, or Android tests.
3. `android-client` remote is rebuilt from a new local branch `android-client-clean`. Do not rewrite the dirty local `android-client` worktree in `/mnt/d/Users/Dan/GoogleDrivePersonal/code/ringdown`; leave that branch and its uncommitted work untouched.
4. Every tracked path in `git diff --name-status main..android-client` must be classified before code edits. No path is allowed to “fall through” the split implicitly.
5. Keep the current `main` versions of `pyproject.toml`, `uv.lock`, `app/tools/change_llm.py`, and the `.worktrees/` ignore rule unless a failing test proves that the rebuilt Android branch needs an additional dependency or merge of a generic fix.
6. Do not land local/editor/device artifact files on either canonical branch: `.vscode/settings.json`, `.wsl/bin/python`, `ringdown_device.preferences_pb`, and `gcp-artifacts-cleanup-policy.json`.
7. Do not touch the unrelated dirty worktrees during this split. Current dirty worktrees are the root `android-client` checkout plus `codex-android-landing-plan`, `fix-api-keys`, `codex/interrupt-sensitivity-high`, `model-46-update`, `model-capabilities`, and `rate-limit-backoff`.
8. No new fallback behavior. If removing Android support from `main` requires a contract break, make that break explicit and update tests/docs to match. Only keep permissive config parsing where that behavior already exists today.

## Ownership Matrix

### Backend-only files that must be restored onto cleaned `main`

- `app/chat.py`
- `app/tools/google_docs.py`
- `app/tools/todo.py`
- `tests/test_google_docs_tool.py`
- `tests/test_error_cases.py`
- `tests/test_todo_tool.py`
- `tests/test_reformat_litellm_log.py`
- `scripts/reformat_litellm_log.py`

These files should be treated as file-level source of truth from `android-client`, then reconciled only where they conflict with the newer local-`main` model-routing line. Do not rely on a short cherry-pick list here; the live diff already spans more than the original commit shortlist (for example `app/tools/google_docs.py` includes both `db159ef` and `c11bc17` changes).

### Android-only files that belong only on `android-client-clean`

- `android/`
- `app/api/mobile.py`
- `app/api/mobile_text.py`
- `app/mobile/__init__.py`
- `app/mobile/smoke.py`
- `app/mobile/text_session_store.py`
- `approve_new_phone.py`
- `docs/voice-smoke-ci.md`
- `tests/test_approve_new_phone.py`
- `tests/test_auto_approve.py`
- `tests/test_mobile_registration.py`
- `tests/test_mobile_smoke.py`
- `tests/test_mobile_text_handshake.py`
- `tests/test_mobile_text_session.py`
- `tests/test_manual_voice_harness.py`
- `tests/test_run_local_voice_smoke.py`
- `tests/test_android_install_script.py`
- `todo-android-local-codex.txt`
- `todo-android-spec/`

### Shared files that must diverge between cleaned `main` and `android-client-clean`

Cleaned `main` keeps the backend-only contract. `android-client-clean` restores the Android-aware variants on top of cleaned `main` for these paths:

- `.env.example`
- `.gitignore`
- `AGENTS.md`
- `README.md`
- `app/api/__init__.py`
- `app/api/websocket.py`
- `app/call_state.py`
- `app/config_schema.py`
- `app/logging_utils.py`
- `app/main.py`
- `app/memory.py`
- `app/mobile/config_store.py`
- `app/settings.py`
- `app/tools/reset.py`
- `cloudrun-deploy.py`
- `config.example.yaml`
- `docs/configuration_guide.md`
- `live_test_call.py`
- `secret-manager.yaml`
- `tests/fixtures/config.test.yaml`
- `tests/live_test_all_functions.py`
- `tests/test_reset_tool.py`
- `tests/test_websocket_interrupt_handling.py`

These files are not “leave off main by default.” They require hunk-level classification:

- Promote the generic/backend hunks to cleaned `main`:
  - `README.md` log-formatting guidance, but not the Android local-audio-assets section
  - `config.example.yaml` and `tests/fixtures/config.test.yaml` Todo tool entries, but not mobile device/text config
  - `app/logging_utils.py` console-to-stdout improvement if it passes the cleaned-main verification suite
  - `app/memory.py` schema bootstrap and optional `source` column if the cleaned-main tests still pass with them
  - `app/tools/reset.py` and `tests/test_reset_tool.py` only if the reset-marker simplification still fits the backend-only contract after tests
- Keep the Android/realtime/mobile hunks only on `android-client-clean`:
  - mobile token auth, realtime bridge plumbing, mobile config/model fields, mobile smoke/deploy hooks, and live mobile diagnostics

### Files that stay on cleaned `main` exactly as they are today unless tests prove otherwise

- `pyproject.toml`
- `uv.lock`
- `app/tools/change_llm.py`
- `tests/test_change_llm_tool.py`

### Files that should not be resurrected

- `authorize_new_phone.py`
- `tests/test_mobile_voice_session.py`
- `.vscode/settings.json`
- `.wsl/bin/python`
- `ringdown_device.preferences_pb`
- `gcp-artifacts-cleanup-policy.json`

`tests/test_mobile_voice_session.py` is deliberately dropped because the Android branch supersedes that older main-only test with the newer mobile text, smoke, and harness suite.

## Cutover Rules

- Create archive refs for both the local and remote `main` tips because they are currently different:
  - `archive/main-local-pre-split-20260326`
  - `archive/main-remote-pre-split-20260326`
  - `archive/android-client-pre-split-20260326`
- Push those archive refs to `origin` before updating `origin/main` or rewriting `origin/android-client`.
- Only `origin/main` and `origin/android-client` move during this project. No other local or remote branches are rebased, reset, or force-pushed.
- Rewrite `origin/android-client` only with `--force-with-lease` pinned to the pre-split observed tip.
- Before publishing either canonical branch, fetch the live remote refs again. If `origin/main` or `origin/android-client` moved while this plan was executing, reconcile that drift before pushing instead of assuming the March 26 snapshot is still current.
- For Android smoke against local code, use `adb reverse` and a locally running backend; do not point the smoke harness at an old deployed branch.

### Task 1: Freeze The Current State And Create Safety Refs

**Files:**
- Modify: none
- Test: git state and archive-ref commands

- [ ] **Step 1: Record the live branch and worktree state**

Run:

```bash
git branch -vv
git worktree list --porcelain
git stash list
git rev-list --left-right --count origin/main...main
git rev-list --left-right --count origin/android-client...android-client
```

Expected: `main` is ahead of `origin/main`, `android-client` matches `origin/android-client`, and the root `android-client` worktree is dirty and must remain untouched.

- [ ] **Step 2: Capture the exact pre-split refs in shell variables**

Run:

```bash
MAIN_LOCAL_PRE=$(git rev-parse --verify main)
MAIN_REMOTE_PRE=$(git rev-parse --verify origin/main)
ANDROID_PRE=$(git rev-parse --verify android-client)
printf 'main local  %s\nmain remote %s\nandroid     %s\n' "$MAIN_LOCAL_PRE" "$MAIN_REMOTE_PRE" "$ANDROID_PRE"
```

Expected: three full hashes print. On March 26, 2026 the expected short forms were `80f2f5d`, `6dd3051`, and `702abb1`.

- [ ] **Step 3: Create local archive refs for every tip that will matter later**

Run:

```bash
git branch archive/main-local-pre-split-20260326 "$(git rev-parse --verify main)"
git branch archive/main-remote-pre-split-20260326 "$(git rev-parse --verify origin/main)"
git branch archive/android-client-pre-split-20260326 "$(git rev-parse --verify android-client)"
```

Expected: all three local archive branches resolve successfully.

- [ ] **Step 4: Push the archive refs to `origin`**

Run:

```bash
git push origin "$(git rev-parse --verify main)":refs/heads/archive/main-local-pre-split-20260326
git push origin "$(git rev-parse --verify origin/main)":refs/heads/archive/main-remote-pre-split-20260326
git push origin "$(git rev-parse --verify android-client)":refs/heads/archive/android-client-pre-split-20260326
```

Expected: the remote archive branches are created without modifying `main` or `android-client`.

- [ ] **Step 5: Verify the archive boundary**

Run:

```bash
git rev-parse --short archive/main-local-pre-split-20260326
git rev-parse --short archive/main-remote-pre-split-20260326
git rev-parse --short archive/android-client-pre-split-20260326
git ls-remote --heads origin archive/main-local-pre-split-20260326 archive/main-remote-pre-split-20260326 archive/android-client-pre-split-20260326
```

Expected: local and remote archive refs resolve to the captured tips.

- [ ] **Step 6: Commit**

No tracked-file commit is needed here. The archive refs are the safety boundary.

### Task 2: Remove The Android Contract From `main`

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `app/main.py`
- Modify: `app/api/__init__.py`
- Modify: `app/config_schema.py`
- Modify: `app/settings.py`
- Modify: `config.example.yaml`
- Modify: `tests/test_app.py`
- Modify: `tests/test_config_schema.py`
- Delete: `app/api/mobile.py`
- Delete: `app/mobile/__init__.py`
- Delete: `app/mobile/config_store.py`
- Delete: `authorize_new_phone.py`
- Delete: `docs/voice-smoke-ci.md`
- Delete: `tests/test_mobile_registration.py`
- Delete: `tests/test_mobile_voice_session.py`
- Delete: `todo-android-spec/mockup-1-pending-approval.html`
- Delete: `todo-android-spec/mockup-2-idle-main.html`
- Delete: `todo-android-spec/mockup-3-voice-active.html`
- Delete: `todo-android-spec/mockup-4-voice-reconnecting.html`
- Delete: `todo-android-spec/mockup-5-chat-session.html`
- Delete: `todo-android-spec/mockup-6-chat-tool-expanded.html`
- Delete: `todo-android-spec/mockup-7-permission-denied.html`
- Delete: `todo-android-spec/mockup-8-background-notification.html`
- Delete: `todo-android-spec/todo-android-spec.txt`
- Test: `tests/test_app.py`
- Test: `tests/test_config_schema.py`

- [ ] **Step 1: Add failing tests for the cleaned-main contract**

Update the existing tests so they prove the desired split:
- `tests/test_app.py` must assert that no registered FastAPI route starts with `/v1/mobile`.
- `tests/test_config_schema.py` must assert that `ConfigModel` no longer exposes `mobile_devices` / `mobileDevices`, while preserving the current permissive `extra="allow"` behavior that already exists on the model today.

- [ ] **Step 2: Run the contract tests and confirm they fail on the current branch**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests/test_app.py tests/test_config_schema.py -v
```

Expected: FAIL because `main` still exposes the mobile router and mobile config schema.

- [ ] **Step 3: Delete the Android-only backend surface from `main`**

Implementation notes:
- Delete `app/api/mobile.py`, `app/mobile/__init__.py`, `app/mobile/config_store.py`, `authorize_new_phone.py`, `docs/voice-smoke-ci.md`, the mobile tests listed above, and the `todo-android-spec/` files.
- Remove the mobile router imports/wiring from `app/main.py` and `app/api/__init__.py`.
- Remove `MobileDeviceConfig` plus the `mobile_devices` validation path from `app/config_schema.py`.
- Remove the Android/mobile settings helpers from `app/settings.py`.
- Remove Android-only examples and instructions from `config.example.yaml`, `README.md`, and `AGENTS.md`.

- [ ] **Step 4: Run the targeted contract checks**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests/test_app.py tests/test_config_schema.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the backend regression slice that should still pass on cleaned `main`**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests/test_app.py tests/test_config_schema.py tests/test_error_cases.py tests/test_google_docs_tool.py tests/test_change_llm_tool.py tests/test_reset_tool.py tests/test_websocket_interrupt_handling.py -v
uv run ruff check
```

Expected: PASS. Any failure here is a real backend regression caused by the cleanup and must be fixed before continuing.

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md README.md app/main.py app/api/__init__.py app/config_schema.py app/settings.py config.example.yaml tests/test_app.py tests/test_config_schema.py
git add -u app/api app/mobile authorize_new_phone.py docs tests todo-android-spec
git commit -m "refactor: remove android contract from main"
```

### Task 3: Port The Backend-Only Improvements Onto Cleaned `main`

**Files:**
- Modify: `app/chat.py`
- Modify: `app/tools/google_docs.py`
- Modify: `tests/test_error_cases.py`
- Modify: `tests/test_google_docs_tool.py`
- Create: `app/tools/todo.py`
- Create: `scripts/reformat_litellm_log.py`
- Create: `tests/test_reformat_litellm_log.py`
- Create: `tests/test_todo_tool.py`
- Test: `tests/test_error_cases.py`
- Test: `tests/test_google_docs_tool.py`
- Test: `tests/test_reformat_litellm_log.py`
- Test: `tests/test_todo_tool.py`

- [ ] **Step 1: Bring over the backend-only tests first**

Copy or recreate the tests so they fail on cleaned `main`:
- the full `android-client` diff for `tests/test_google_docs_tool.py`, including the SearchGoogleDrive pagination/runtime-limit assertions from `db159ef` and the folder-exclusion/read-scope assertions from `c11bc17`
- `tests/test_todo_tool.py`
- `tests/test_reformat_litellm_log.py`
- the rate-limit retry/backoff coverage in `tests/test_error_cases.py`

- [ ] **Step 2: Run the targeted tests and confirm they are red**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests/test_google_docs_tool.py tests/test_todo_tool.py tests/test_reformat_litellm_log.py tests/test_error_cases.py -v
```

Expected: FAIL because the Todo tool, log formatter, the full Google Docs search/read improvements, and rate-limit retry logic are not yet present on cleaned `main`.

- [ ] **Step 3: Restore the backend-only source-of-truth files from `android-client` and reconcile them on top of local `main`**

Implementation notes:
- Use the live file diff, not a four-commit shortlist, as the source of truth for `app/chat.py`, `app/tools/google_docs.py`, `tests/test_google_docs_tool.py`, and `tests/test_error_cases.py`.
- Restore these paths from `android-client` first:

```bash
git restore --source=android-client -- app/chat.py app/tools/google_docs.py app/tools/todo.py scripts/reformat_litellm_log.py tests/test_google_docs_tool.py tests/test_error_cases.py tests/test_todo_tool.py tests/test_reformat_litellm_log.py
```

- Then reconcile `app/chat.py` so it keeps the current local-`main` model-routing line while also preserving the backend-only logging and rate-limit work from `android-client`.
- Ensure `app/tools/google_docs.py` keeps both the broader Drive read/search behavior and the `SearchGoogleDrive` pagination/runtime-limit improvements; those are currently split across multiple `android-client` commits and are easy to under-port if you cherry-pick by commit message.
- Do not touch `pyproject.toml`, `uv.lock`, `app/tools/change_llm.py`, or `tests/test_change_llm_tool.py` in this task.

- [ ] **Step 4: Re-run the targeted backend-only tests**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests/test_google_docs_tool.py tests/test_todo_tool.py tests/test_reformat_litellm_log.py tests/test_error_cases.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the cleaned-main regression slice before the shared-file hunk pass**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests/test_app.py tests/test_config_schema.py tests/test_google_docs_tool.py tests/test_todo_tool.py tests/test_reformat_litellm_log.py tests/test_error_cases.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/chat.py app/tools/google_docs.py app/tools/todo.py scripts/reformat_litellm_log.py tests/test_google_docs_tool.py tests/test_todo_tool.py tests/test_reformat_litellm_log.py tests/test_error_cases.py
git commit -m "feat: keep backend improvements on cleaned main"
```

### Task 4: Reconcile Shared File Hunks Before Publishing `main`

**Files:**
- Modify: `README.md`
- Modify: `config.example.yaml`
- Modify: `tests/fixtures/config.test.yaml`
- Modify: `app/logging_utils.py`
- Modify: `app/memory.py`
- Modify: `app/tools/reset.py` only if the cleaned-main tests prove the simplified reset marker still belongs on `main`
- Modify: `tests/test_reset_tool.py` only if `app/tools/reset.py` changes on `main`
- Modify: `.gitignore` only if the audit proves a generic ignore improvement belongs on `main`
- Test: git diff classification and any targeted checks needed by audit findings

- [ ] **Step 1: List every file that still differs between cleaned `main` and `android-client`**

Run:

```bash
git diff --name-status HEAD..android-client
```

Expected: only the paths named in the ownership matrix differ. If any additional tracked file appears, stop and classify it before continuing.

- [ ] **Step 2: Promote the generic shared-file hunks that belong on cleaned `main`**

Explicitly inspect these shared files and keep the backend-safe hunks on `main`:
- `README.md`
- `config.example.yaml`
- `tests/fixtures/config.test.yaml`
- `app/logging_utils.py`
- `app/memory.py`
- `app/tools/reset.py`
- `tests/test_reset_tool.py`

Implementation notes:
- Keep the `README.md` log-formatting section on `main`, but do not add the Android local-audio-assets section.
- Keep the Todo tool config entries in `config.example.yaml` and `tests/fixtures/config.test.yaml`, but do not add `mobileDevices`, `mobileText`, or mobile smoke variables.
- Promote the `app/logging_utils.py` stdout console-handler change if it does not break the cleaned-main suite.
- Promote the `app/memory.py` schema bootstrap and optional `source` column if the cleaned-main suite still passes with them.
- Only promote the `app/tools/reset.py`/`tests/test_reset_tool.py` reset-marker simplification if the backend-only contract still wants it after tests. If it exists only to satisfy the Android/live harness path, keep it off `main`.
- Leave `.env.example`, `AGENTS.md`, `app/api/__init__.py`, `app/api/websocket.py`, `app/call_state.py`, `app/config_schema.py`, `app/main.py`, `app/mobile/config_store.py`, `app/settings.py`, `cloudrun-deploy.py`, `docs/configuration_guide.md`, `live_test_call.py`, `secret-manager.yaml`, `tests/live_test_all_functions.py`, and `tests/test_websocket_interrupt_handling.py` on the cleaned-main side unless a specific generic hunk proves otherwise.

- [ ] **Step 3: Run the final cleaned-main verification suite**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests
uv run ruff check
```

Expected: PASS. Any failure means a shared-file hunk was classified incorrectly or an earlier transplant is incomplete.

- [ ] **Step 4: Verify the cleaned-main dependency baseline stays intact**

Run:

```bash
git diff --name-status archive/main-local-pre-split-20260326..HEAD -- pyproject.toml uv.lock app/tools/change_llm.py tests/test_change_llm_tool.py
```

Expected: no diff unless a test-driven fix explicitly required one.

- [ ] **Step 5: Commit**

If Step 2, Step 3, or Step 4 changed tracked files, commit them now:

```bash
git add -A
git commit -m "chore: finish cleaned main audit"
```

If nothing changed, there is no commit in this task.

### Task 5: Publish The Cleaned `main`

**Files:**
- Modify: none
- Test: branch alignment commands

- [ ] **Step 1: Refresh `origin` and detect remote drift before touching the publish worktree**

Run:

```bash
git fetch origin main android-client
ORIGIN_MAIN_LIVE=$(git rev-parse --verify origin/main)
ARCHIVED_MAIN_REMOTE=$(git rev-parse --verify archive/main-remote-pre-split-20260326)
printf 'origin/main live     %s\narchived remote tip %s\n' "$ORIGIN_MAIN_LIVE" "$ARCHIVED_MAIN_REMOTE"
```

Expected: if the hashes still match, continue. If `origin/main` advanced, rebase `trycycle-main-android-split` onto the new `origin/main`, then re-run Task 4’s full cleaned-main verification suite before publishing.

- [ ] **Step 2: Fast-forward the dedicated `main` worktree to the cleaned implementation**

Run:

```bash
git -C /mnt/d/Users/Dan/GoogleDrivePersonal/code/ringdown/.worktrees/main-deploy merge --ff-only trycycle-main-android-split
```

Expected: fast-forward succeeds. If it does not, the implementation branch is no longer a pure descendant of `main` and must be fixed before publishing.

- [ ] **Step 3: Push the cleaned `main` to `origin`**

Run:

```bash
git -C /mnt/d/Users/Dan/GoogleDrivePersonal/code/ringdown/.worktrees/main-deploy push origin main
```

Expected: push succeeds without force.

- [ ] **Step 4: Verify `main` and `origin/main` are aligned**

Run:

```bash
git rev-list --left-right --count origin/main...main
git rev-parse --short main
git rev-parse --short origin/main
```

Expected: divergence count is `0	0`, and the short hashes match.

- [ ] **Step 5: Commit**

No tracked-file commit is needed here.

### Task 6: Restore The Android-Only Files On Top Of The Published `main`

**Files:**
- Modify: none yet; this task creates the Android-owned tree on a new branch
- Create: `android/`
- Create: `app/api/mobile.py`
- Create: `app/api/mobile_text.py`
- Create: `app/mobile/__init__.py`
- Create: `app/mobile/smoke.py`
- Create: `app/mobile/text_session_store.py`
- Create: `approve_new_phone.py`
- Create: `docs/voice-smoke-ci.md`
- Create: `tests/test_approve_new_phone.py`
- Create: `tests/test_auto_approve.py`
- Create: `tests/test_mobile_registration.py`
- Create: `tests/test_mobile_smoke.py`
- Create: `tests/test_mobile_text_handshake.py`
- Create: `tests/test_mobile_text_session.py`
- Create: `tests/test_manual_voice_harness.py`
- Create: `tests/test_run_local_voice_smoke.py`
- Create: `tests/test_android_install_script.py`
- Create: `todo-android-local-codex.txt`
- Create: `todo-android-spec/`
- Test: file restore and branch-base commands

- [ ] **Step 1: Start the reconstruction branch from the published `main`**

Run:

```bash
git switch -c android-client-clean main
git merge-base --is-ancestor main HEAD
git ls-tree -r --name-only HEAD | rg '^android/' || true
```

Expected: the new branch points at cleaned `main`, and the `android/` tree is still absent before restore.

- [ ] **Step 2: Restore only the Android-only files from `android-client`**

Run:

```bash
git restore --source=android-client -- android
git restore --source=android-client -- app/api/mobile.py app/api/mobile_text.py app/mobile/__init__.py app/mobile/smoke.py app/mobile/text_session_store.py approve_new_phone.py docs/voice-smoke-ci.md tests/test_approve_new_phone.py tests/test_auto_approve.py tests/test_mobile_registration.py tests/test_mobile_smoke.py tests/test_mobile_text_handshake.py tests/test_mobile_text_session.py tests/test_manual_voice_harness.py tests/test_run_local_voice_smoke.py tests/test_android_install_script.py todo-android-local-codex.txt todo-android-spec
```

Expected: Android-only files are present again, but shared files still match cleaned `main`.

- [ ] **Step 3: Restore the renamed approval helper boundary correctly**

Implementation notes:
- Do not resurrect `authorize_new_phone.py`.
- Ensure `approve_new_phone.py` and `tests/test_approve_new_phone.py` are the active pair.
- Keep `tests/test_mobile_voice_session.py` deleted.

- [ ] **Step 4: Commit**

```bash
git add android app/api/mobile.py app/api/mobile_text.py app/mobile/__init__.py app/mobile/smoke.py app/mobile/text_session_store.py approve_new_phone.py docs/voice-smoke-ci.md tests/test_approve_new_phone.py tests/test_auto_approve.py tests/test_mobile_registration.py tests/test_mobile_smoke.py tests/test_mobile_text_handshake.py tests/test_mobile_text_session.py tests/test_manual_voice_harness.py tests/test_run_local_voice_smoke.py tests/test_android_install_script.py todo-android-local-codex.txt todo-android-spec
git commit -m "feat: restore android-owned files"
```

### Task 7: Restore Android-Owned Shared File Deltas And Reconcile Them With Cleaned `main`

**Files:**
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `app/api/__init__.py`
- Modify: `app/api/websocket.py`
- Modify: `app/call_state.py`
- Modify: `app/config_schema.py`
- Modify: `app/logging_utils.py`
- Modify: `app/main.py`
- Modify: `app/memory.py`
- Modify: `app/mobile/config_store.py`
- Modify: `app/settings.py`
- Modify: `app/tools/reset.py`
- Modify: `cloudrun-deploy.py`
- Modify: `config.example.yaml`
- Modify: `docs/configuration_guide.md`
- Modify: `live_test_call.py`
- Modify: `secret-manager.yaml`
- Modify: `tests/fixtures/config.test.yaml`
- Modify: `tests/live_test_all_functions.py`
- Modify: `tests/test_reset_tool.py`
- Modify: `tests/test_websocket_interrupt_handling.py`
- Test: `tests/test_approve_new_phone.py`
- Test: `tests/test_auto_approve.py`
- Test: `tests/test_mobile_registration.py`
- Test: `tests/test_mobile_text_session.py`
- Test: `tests/test_mobile_text_handshake.py`
- Test: `tests/test_mobile_smoke.py`
- Test: `tests/test_manual_voice_harness.py`
- Test: `tests/test_run_local_voice_smoke.py`
- Test: `tests/test_android_install_script.py`
- Test: `tests/test_reset_tool.py`
- Test: `tests/test_websocket_interrupt_handling.py`

- [ ] **Step 1: Restore the Android-owned variants of the shared files from `android-client`**

Run:

```bash
git restore --source=android-client -- .env.example .gitignore AGENTS.md README.md app/api/__init__.py app/api/websocket.py app/call_state.py app/config_schema.py app/logging_utils.py app/main.py app/memory.py app/mobile/config_store.py app/settings.py app/tools/reset.py cloudrun-deploy.py config.example.yaml docs/configuration_guide.md live_test_call.py secret-manager.yaml tests/fixtures/config.test.yaml tests/live_test_all_functions.py tests/test_reset_tool.py tests/test_websocket_interrupt_handling.py
```

Expected: the Android/shared backend wiring comes back, but `pyproject.toml`, `uv.lock`, `app/tools/change_llm.py`, and `tests/test_change_llm_tool.py` still match cleaned `main`.

- [ ] **Step 2: Reconcile each restored shared file against cleaned `main` deliberately**

Implementation notes:
- Keep the cleaned-main backend-only improvements in `app/chat.py`, `app/tools/google_docs.py`, `app/tools/todo.py`, `scripts/reformat_litellm_log.py`, and every generic hunk already promoted onto `main` in Task 4 (`README.md`, `config.example.yaml`, `tests/fixtures/config.test.yaml`, and any kept portions of `app/logging_utils.py`, `app/memory.py`, and `app/tools/reset.py`).
- Keep the current `main` `.worktrees/` ignore rule in `.gitignore`, but also keep Android/harness ignore entries that are still useful on the Android branch.
- Keep `cloudrun-deploy.py` mobile smoke behavior, `LIVE_TEST_MOBILE_DEVICE_ID`, and `secret-manager.yaml` changes on the Android branch only.
- Keep the cleaned-main dependency baseline; do not restore the old `android-client` `pyproject.toml` or `uv.lock`.
- If a missing import or failing test proves an Android dependency is absent, add only the minimum required dependency to `pyproject.toml` and run `uv sync`.

- [ ] **Step 3: Run the targeted Android/shared Python tests**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests/test_approve_new_phone.py tests/test_auto_approve.py tests/test_mobile_registration.py tests/test_mobile_text_session.py tests/test_mobile_text_handshake.py tests/test_mobile_smoke.py tests/test_manual_voice_harness.py tests/test_run_local_voice_smoke.py tests/test_android_install_script.py tests/test_reset_tool.py tests/test_websocket_interrupt_handling.py -v
```

Expected: PASS. Any failure here means a required shared-file delta was missed or reconciled incorrectly.

- [ ] **Step 4: Commit**

```bash
git add .env.example .gitignore AGENTS.md README.md app/api/__init__.py app/api/websocket.py app/call_state.py app/config_schema.py app/logging_utils.py app/main.py app/memory.py app/mobile/config_store.py app/settings.py app/tools/reset.py cloudrun-deploy.py config.example.yaml docs/configuration_guide.md live_test_call.py secret-manager.yaml tests/fixtures/config.test.yaml tests/live_test_all_functions.py tests/test_reset_tool.py tests/test_websocket_interrupt_handling.py pyproject.toml uv.lock
git commit -m "feat: reconcile android shared backend surface"
```

### Task 8: Run The Full Verification Matrix And Publish The Canonical Android Branch

**Files:**
- Modify: whichever files need final test-driven fixes from Task 7
- Test: full Python and Android verification matrix

- [ ] **Step 1: Run the full Python suite on `android-client-clean`**

Run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run pytest tests
uv run ruff check
```

Expected: PASS.

- [ ] **Step 2: Run the Android unit and connected suites**

Run:

```bash
adb devices
bash android/scripts/gradle.sh :app:testDebugUnitTest
bash android/scripts/gradle.sh :app:connectedVoiceMvpAndroidTest
```

Expected: PASS. If more than one handset/emulator is attached, export `ANDROID_SERIAL` before the Gradle commands so the connected suite targets the intended device.

- [ ] **Step 3: Run the local voice smoke against the rebuilt backend**

Run in shell 1:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
adb reverse tcp:8000 tcp:8000
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Run in shell 2:

```bash
bash android/scripts/run-voice-smoke.sh --backend http://127.0.0.1:8000
```

Expected: the smoke reaches the local backend through `adb reverse` and exits successfully.

- [ ] **Step 4: Commit any final test-driven fixes**

```bash
git add -A
git commit -m "fix: finish android branch split verification"
```

- [ ] **Step 5: Refresh the remote refs and publish the canonical Android branch**

Run:

```bash
git fetch origin main android-client
ANDROID_REMOTE_PRE=$(git rev-parse --verify archive/android-client-pre-split-20260326)
git push origin android-client-clean
git push origin android-client-clean:android-client --force-with-lease=refs/heads/android-client:"$ANDROID_REMOTE_PRE"
```

Expected: `origin/android-client-clean` is created, and `origin/android-client` is rewritten only if it still points at the archived pre-split tip. If the lease fails because another agent moved `origin/android-client`, archive that new remote tip, diff it against `android-client-clean`, reconcile any intentional new work, and only then retry the force-push.

- [ ] **Step 6: Verify final branch topology**

Run:

```bash
git rev-list --left-right --count origin/main...main
git rev-parse --short origin/android-client
git merge-base --is-ancestor origin/main origin/android-client
git ls-remote --heads origin android-client android-client-clean archive/android-client-pre-split-20260326 archive/main-local-pre-split-20260326 archive/main-remote-pre-split-20260326
```

Expected: `main` and `origin/main` are aligned, the rebuilt Android branch is based on the published cleaned `main`, and all archive refs still exist.
