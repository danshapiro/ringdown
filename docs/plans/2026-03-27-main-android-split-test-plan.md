# Main And Android Split Test Plan

The approved testing direction still holds against the finalized implementation plan: use automated checks only, lead with the red checks already identified in the plan, reuse the highest-fidelity existing harnesses where they exist, and prove behavior through the real user-facing surfaces or branch topology rather than internals alone. The plan adds no new paid or external dependencies beyond the execution prerequisites already called out in the implementation plan: `OPENAI_API_KEY` for the Android Python lane, plus `adb`, a connected handset, and `ANDROID_SERIAL` when needed for the Android connected and smoke lanes.

## Harness requirements

### `git-split-audit` (`existing`, strengthen)
- What it does: runs git commands against frozen archive refs and candidate branches to verify ownership boundaries, dependency-baseline preservation, and final publication topology.
- What it exposes: branch/ref hashes, `git diff --name-status`, `git merge-base --is-ancestor`, `git rev-list --left-right --count`, and `--force-with-lease` guard inputs.
- Estimated complexity to build: low. The commands already exist in the implementation plan; the work is to treat them as explicit acceptance checks instead of ad hoc operator checks.
- Tests that depend on it: 1, 6, 8, 12.

### `pytest-http-config-contract` (`existing`, extend)
- What it does: exercises the real FastAPI application and config schema with `TestClient` and Pydantic validation.
- What it exposes: registered HTTP/WebSocket routes, HTTP responses, and schema-owned versus extra config fields.
- Estimated complexity to build: low. `tests/test_app.py` and `tests/test_config_schema.py` already exist and need stronger contract assertions.
- Tests that depend on it: 2, 3.

### `pytest-backend-tools` (`existing`, extend)
- What it does: runs backend pytest coverage for the shared backend tools and error-handling flows that must survive the split onto cleaned `main`.
- What it exposes: tool registration and execution results, retry/backoff behavior, formatter output, and end-to-end backend regression slices.
- Estimated complexity to build: medium. Existing suites need to be extended and the missing Todo/log-formatter suites need to be restored from the archived Android line.
- Tests that depend on it: 4, 5, 7.

### `pytest-mobile-http-ws` (`existing`, restore and extend)
- What it does: exercises the rebuilt Android-support backend surface through HTTP registration endpoints and the Twilio/mobile WebSocket flows.
- What it exposes: `POST /v1/mobile/devices/register`, mobile text/session behavior, reset-tool behavior through streaming, and Twilio interrupt handling.
- Estimated complexity to build: medium. The current worktree still has the old mobile backend files and tests, but the final Android branch requires restoring the newer mobile test set named in the implementation plan.
- Tests that depend on it: 9, 10.

### `android-gradle-device` (`existing`, restore)
- What it does: runs the Android JVM/unit and connected instrumentation suites from the restored `android/` tree against a tethered device or emulator.
- What it exposes: Gradle test results for `:app:testDebugUnitTest` and `:app:connectedVoiceMvpAndroidTest`.
- Estimated complexity to build: medium. The current worktree has no `android/` directory, so the harness becomes available only after Task 5 restores the Android-owned files.
- Tests that depend on it: 11.

### `android-local-voice-smoke` (`existing`, restore)
- What it does: runs the end-to-end voice smoke through the actual Android app against a locally served backend using `adb reverse`.
- What it exposes: the smoke-script exit status over the real backend HTTP surface and real device transport path.
- Estimated complexity to build: medium. It depends on the restored Android tree, `adb`, a device, and a local backend started from the rebuilt branch.
- Tests that depend on it: 11.

## Test plan

1. **Name**: Archive refs freeze the exact pre-split source tips before any reconstruction work.
   **Type**: invariant
   **Disposition**: existing
   **Harness**: `git-split-audit`
   **Preconditions**: Work starts in the clean trycycle worktree on branch `trycycle-main-android-split`; local `main`, `origin/main`, `android-client`, and `origin/android-client` still point to the live refs described in the implementation plan.
   **Actions**: Run the Task 1 commands that capture the live hashes, create `archive/main-local-pre-split-20260326`, `archive/main-remote-pre-split-20260326`, and `archive/android-client-pre-split-20260326`, then push those archive refs to `origin` and verify them locally and remotely.
   **Expected outcome**: The archive refs resolve to the captured tips locally and on `origin`, and later checks use those archive refs instead of mutable live branches. Source of truth: implementation plan `Strategy And Boundary Decisions` item 1 and `Cutover Rules`.
   **Interactions**: Local git refs, remote `origin`, all later differential checks.

2. **Name**: Cleaned `main` rejects the Android HTTP/WebSocket contract and no longer owns mobile schema fields.
   **Type**: regression
   **Disposition**: extend
   **Harness**: `pytest-http-config-contract`
   **Preconditions**: The worktree is still in the pre-cleanup state where `app.main` includes the mobile router and `ConfigModel` still owns `mobile_devices`.
   **Actions**: Extend `tests/test_app.py` to enumerate registered FastAPI routes and fail if any path starts with `/v1/mobile` or `/ws/mobile`. Extend `tests/test_config_schema.py` to fail if `ConfigModel.model_fields` still defines `mobile_devices` or `mobileDevices`, while still accepting config payloads that contain those keys as extras. Run `UV_PROJECT_ENVIRONMENT=.venv-wsl uv run pytest tests/test_app.py tests/test_config_schema.py -v`.
   **Expected outcome**: The initial run fails on the current branch, proving the split contract is not satisfied yet. After Task 2 implementation, the same tests pass. Source of truth: implementation plan `Strategy And Boundary Decisions` item 2 and Task 2 Step 1.
   **Interactions**: FastAPI router registration, Pydantic schema ownership, config parsing.

3. **Name**: Cleaned `main` serves only the backend contract after mobile removal.
   **Type**: integration
   **Disposition**: extend
   **Harness**: `pytest-http-config-contract`
   **Preconditions**: Task 2 code changes are applied on `trycycle-main-android-split`; mobile files/routes/settings have been removed from cleaned `main`.
   **Actions**: Re-run `UV_PROJECT_ENVIRONMENT=.venv-wsl uv run pytest tests/test_app.py tests/test_config_schema.py -v`.
   **Expected outcome**: The route census contains no `/v1/mobile*` or `/ws/mobile*` routes, and repository config still validates with `mobile_devices` treated as permissive extras rather than schema-owned fields. Source of truth: implementation plan Task 2 Step 4 and the repository’s explicit `extra="allow"` config behavior.
   **Interactions**: FastAPI app assembly, Pydantic config validation, repository `config.yaml`.

4. **Name**: Backend-only improvements missing from cleaned `main` go red before they are restored.
   **Type**: regression
   **Disposition**: extend
   **Harness**: `pytest-backend-tools`
   **Preconditions**: Task 2 is complete, but Task 3 has not yet restored the backend-only improvements from the archived Android line.
   **Actions**: Restore or recreate the backend-only tests first: the fuller `tests/test_google_docs_tool.py` coverage, `tests/test_todo_tool.py`, `tests/test_reformat_litellm_log.py`, and the rate-limit retry/backoff additions in `tests/test_error_cases.py`. Run `UV_PROJECT_ENVIRONMENT=.venv-wsl uv run pytest tests/test_google_docs_tool.py tests/test_todo_tool.py tests/test_reformat_litellm_log.py tests/test_error_cases.py -v`.
   **Expected outcome**: The targeted slice fails before code restoration, proving cleaned `main` is still missing the intended backend functionality. Source of truth: implementation plan Task 3 Step 1 and Step 2 plus the `Ownership Matrix` backend-only file list.
   **Interactions**: Google Docs tool surface, Todo tool surface, LiteLLM log formatter CLI/script behavior, backend retry logic.

5. **Name**: Cleaned `main` preserves the backend-only tool and error-handling improvements from `android-client`.
   **Type**: integration
   **Disposition**: extend
   **Harness**: `pytest-backend-tools`
   **Preconditions**: Task 3 has restored `app/chat.py`, `app/tools/google_docs.py`, `app/tools/todo.py`, `scripts/reformat_litellm_log.py`, and the related tests onto cleaned `main`.
   **Actions**: Run `UV_PROJECT_ENVIRONMENT=.venv-wsl uv run pytest tests/test_google_docs_tool.py tests/test_todo_tool.py tests/test_reformat_litellm_log.py tests/test_error_cases.py -v`, then run `UV_PROJECT_ENVIRONMENT=.venv-wsl uv run pytest tests/test_app.py tests/test_config_schema.py tests/test_google_docs_tool.py tests/test_todo_tool.py tests/test_reformat_litellm_log.py tests/test_error_cases.py -v`.
   **Expected outcome**: The backend-only slice passes, proving cleaned `main` now exposes the improved Google Docs behavior, Todo tool, log formatter, and rate-limit handling without reintroducing Android behavior. Source of truth: implementation plan Task 3 Steps 4 and 5 and the `Ownership Matrix` backend-only list.
   **Interactions**: Tool registry, Google Docs integration layer, chat error handling, log formatting script.

6. **Name**: Shared-file reconciliation on cleaned `main` keeps only generic hunks and no extra tracked paths leak through the split.
   **Type**: differential
   **Disposition**: new
   **Harness**: `git-split-audit`
   **Preconditions**: Task 4 shared-file reconciliation is complete on `trycycle-main-android-split`.
   **Actions**: Run `git diff --name-status HEAD..archive/android-client-pre-split-20260326`, inspect that only the ownership-matrix shared and Android-owned paths differ, then run `git diff --name-status archive/main-local-pre-split-20260326..HEAD -- pyproject.toml uv.lock app/tools/change_llm.py tests/test_change_llm_tool.py`.
   **Expected outcome**: Only the planned shared/Android-owned files still differ from the archived Android snapshot, and the dependency baseline plus `change_llm` files remain unchanged unless a previously failing test proved otherwise. Source of truth: implementation plan `Ownership Matrix`, Task 4 Step 1, and Task 4 Step 5.
   **Interactions**: Archive refs, cleaned-main file graph, dependency baseline.

7. **Name**: Cleaned `main` passes the full backend verification suite after the shared-file audit.
   **Type**: scenario
   **Disposition**: existing
   **Harness**: `pytest-backend-tools`
   **Preconditions**: Tasks 2 through 4 are complete on `trycycle-main-android-split`.
   **Actions**: Run `UV_PROJECT_ENVIRONMENT=.venv-wsl uv run pytest tests` and `UV_PROJECT_ENVIRONMENT=.venv-wsl uv run ruff check`.
   **Expected outcome**: The entire Python suite and lint pass on cleaned `main`. Any failure is treated as a real backend regression caused by the split or hunk misclassification. Source of truth: implementation plan Task 4 Step 4 and repo instructions in `AGENTS.md`.
   **Interactions**: Entire backend Python surface, repo-wide lint rules.

8. **Name**: The rebuilt Android branch restores exactly the Android-owned files and keeps the cleaned-main base intact.
   **Type**: differential
   **Disposition**: new
   **Harness**: `git-split-audit`
   **Preconditions**: `android-client-rebuilt` has been created from the cleaned-main candidate and Task 5 restore work has been applied.
   **Actions**: On `android-client-rebuilt`, run the Task 5 restore commands, verify `git merge-base --is-ancestor trycycle-main-android-split HEAD` succeeds, verify the restored Android-owned files exist, and confirm that `authorize_new_phone.py` stays absent while `approve_new_phone.py` is the active helper after restoration.
   **Expected outcome**: The Android branch is rooted on the cleaned-main candidate, Android-owned files are restored from the archive, `authorize_new_phone.py` is not resurrected, and `tests/test_mobile_voice_session.py` remains absent. Source of truth: implementation plan Task 5, `Ownership Matrix`, and `Files that should not be resurrected`.
   **Interactions**: Branch ancestry, archived Android snapshot, restored Android/backend file set.

9. **Name**: The rebuilt Android backend exposes the Android/mobile contract through the real backend surfaces.
   **Type**: integration
   **Disposition**: extend
   **Harness**: `pytest-mobile-http-ws`
   **Preconditions**: Task 6 has restored the Android-aware shared files on `android-client-rebuilt`; `OPENAI_API_KEY` is present in the environment.
   **Actions**: Run `test -n "${OPENAI_API_KEY:-}"` and then `UV_PROJECT_ENVIRONMENT=.venv-wsl uv run pytest tests/test_approve_new_phone.py tests/test_auto_approve.py tests/test_mobile_registration.py tests/test_mobile_text_session.py tests/test_mobile_text_handshake.py tests/test_mobile_smoke.py tests/test_manual_voice_harness.py tests/test_run_local_voice_smoke.py tests/test_android_install_script.py tests/test_reset_tool.py tests/test_websocket_interrupt_handling.py -v`.
   **Expected outcome**: The Android/shared Python slice passes, proving the branch once again supports device approval, device registration, mobile text/session flows, local voice harness helpers, Android install helpers, reset streaming, and interrupt-safe WebSocket behavior. Source of truth: implementation plan Task 6 Step 4 and the Android/shared file ownership matrix.
   **Interactions**: Mobile registration endpoint, mobile text/session backend surface, Twilio WebSocket flow, approval helper CLI, Android helper scripts.

10. **Name**: `android-client-rebuilt` passes the full Python regression suite without weakening the cleaned-main baseline.
   **Type**: scenario
   **Disposition**: existing
   **Harness**: `pytest-mobile-http-ws`
   **Preconditions**: Task 6 targeted fixes are complete on `android-client-rebuilt`; any dependency additions were added minimally and synchronized through `pyproject.toml` and `uv.lock` only if tests required them.
   **Actions**: Run `test -n "${OPENAI_API_KEY:-}"`, `UV_PROJECT_ENVIRONMENT=.venv-wsl uv run pytest tests`, and `UV_PROJECT_ENVIRONMENT=.venv-wsl uv run ruff check`.
   **Expected outcome**: The full Python suite and lint pass on the rebuilt Android branch. Source of truth: implementation plan Task 7 Step 1 and repo instructions in `AGENTS.md`.
   **Interactions**: Entire Android-aware Python/backend surface, repo-wide lint rules.

11. **Name**: The restored Android app passes unit, connected, and local voice-smoke verification against the rebuilt backend.
   **Type**: scenario
   **Disposition**: existing
   **Harness**: `android-gradle-device`, `android-local-voice-smoke`
   **Preconditions**: `android/` has been restored; `adb devices` shows the target handset or emulator; `ANDROID_SERIAL` is exported when more than one device is attached; the rebuilt branch’s backend can be served locally.
   **Actions**: Run `adb devices`, `bash android/scripts/gradle.sh :app:testDebugUnitTest`, and `bash android/scripts/gradle.sh :app:connectedVoiceMvpAndroidTest`. Then run `adb reverse tcp:8000 tcp:8000`, start `UV_PROJECT_ENVIRONMENT=.venv-wsl uv run uvicorn app.main:app --host 0.0.0.0 --port 8000` in one shell, and run `bash android/scripts/run-voice-smoke.sh --backend http://127.0.0.1:8000` in another.
   **Expected outcome**: Both Gradle suites pass and the voice smoke exits successfully against the local backend, proving the rebuilt Android app and backend interoperate through the real device path. Source of truth: implementation plan Task 7 Steps 2 and 3 and repo instructions in `AGENTS.md`.
   **Interactions**: Android Gradle build/test system, adb device bridge, local FastAPI backend, Android app transport path.

12. **Name**: Coordinated cutover preserves branch alignment, publication guards, and the required final topology.
   **Type**: invariant
   **Disposition**: existing
   **Harness**: `git-split-audit`
   **Preconditions**: Tests 7, 10, and 11 are green; `.worktrees/main-deploy` is clean; archive refs still exist.
   **Actions**: Run the Task 8 commands that fetch live remote refs, compare them to the archived remote tips, fast-forward `.worktrees/main-deploy` to `trycycle-main-android-split`, push `origin main`, push `android-client-rebuilt:android-client` with `--force-with-lease` pinned to the archived Android remote tip, then run the final topology checks.
   **Expected outcome**: `main` and `origin/main` are aligned, `android-client-rebuilt` and `origin/android-client` are aligned, `origin/main` is an ancestor of the rebuilt Android branch, the root checkout remains on the untouched dirty `android-client` worktree, and the archive refs still exist. Source of truth: implementation plan `Cutover Rules` and Task 8.
   **Interactions**: Local `main` worktree, remote canonical branches, force-with-lease publication guard, untouched root `android-client` worktree.

## Coverage summary

Covered action space:
- Git ref and branch actions required by the split: archive creation, ancestry checks, diff ownership checks, fast-forward publication, and force-with-lease publication.
- FastAPI backend actions on cleaned `main`: app startup, route registration, Twilio `/twiml`, and config-model validation behavior.
- Backend tool actions that must remain on `main`: Google Docs search/read improvements, Todo tool behavior, LiteLLM log reformatting, error/retry handling, and full pytest plus ruff.
- Android/backend actions on `android-client-rebuilt`: device approval helper, mobile device registration, mobile text/session and interrupt handling, Android helper scripts, full Python regression lane, Gradle JVM tests, connected instrumentation, and local voice smoke against a local backend.

Explicit exclusions per strategy:
- No manual QA or human inspection gates.
- No production Cloud Run deploy or Twilio live-call acceptance as part of this split’s required verification matrix.
- No assertions derived solely from private implementation details when a user-visible surface exists.

Risks from exclusions:
- Because the required matrix stops at local automated and device-backed checks, production-only issues in Cloud Run, Twilio webhooks, or secret wiring could still exist after publication.
- The Android acceptance lane depends on the restored test harness matching the archived branch well enough to execute on current local tooling; if the restored Android tree has drifted against the current host environment, harness repair may be needed before the highest-fidelity Android checks can run.
