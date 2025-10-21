# Voice Smoke CI Integration

This note captures the tasks needed to run the Phase 3 voice smoke test (`VoiceMvpSuite`) in an automated environment.

## Prerequisites

- Android SDK and platform tools available on the runner; reuse `android/scripts/setup-sdk.sh` if the host is fresh.
- Java 17, with `JAVA_HOME` exported so the Gradle wrapper can locate it.
- A connected device or emulator that shows up under `adb devices`.
  - Set `ANDROID_SERIAL` when more than one device might be present.
- `.env` populated with production/staging endpoints as documented in `android/app/app/build.gradle.kts`.

## Command Reference

Run the instrumentation smoke test via the helper script. The backend URL must be HTTPS and point at the deployed Ringdown backend.

```bash
export RINGDOWN_BACKEND_URL="https://danbot-twilio-bkvo7niota-uw.a.run.app/"
export ANDROID_SERIAL="${ANDROID_SERIAL:?set to emulator-5554 or real device serial}"
bash android/scripts/run-voice-smoke.sh
```

Optional overrides:

- `RINGDOWN_DEVICE_ID_OVERRIDE` (or `--device-id`) to pass a specific instrumented device identifier. Defaults to `instrumentation-device`.
- `--backend` can override `RINGDOWN_BACKEND_URL` when running manually.

For full production validation (assemble, install, and run both suites), call:

```bash
export RINGDOWN_BACKEND_URL="https://danbot-twilio-bkvo7niota-uw.a.run.app/"
export ANDROID_SERIAL="${ANDROID_SERIAL:?set to emulator-5554 or real device serial}"
bash android/scripts/run-production-instrumentation.sh
```

## CI Hook Sketch

1. Provision the Android SDK and accept licenses (use `android/scripts/setup-sdk.sh`).
2. Start or connect to an emulator/device and export `ANDROID_SERIAL`.
3. Export `RINGDOWN_BACKEND_URL` for the production backend target.
4. Execute `bash android/scripts/run-voice-smoke.sh`.
5. Archive Gradle reports from `android/app/app/build/reports/androidTests` for debugging.

Example GitHub Actions step (pseudo):

```yaml
      - name: Run voice smoke instrumentation
        env:
          ANDROID_SERIAL: emulator-5554
          RINGDOWN_BACKEND_URL: ${{ secrets.RINGDOWN_PROD_BACKEND }}
        run: |
          bash android/scripts/setup-sdk.sh
          bash android/scripts/run-voice-smoke.sh
```

## Follow-ups

- Add emulator boot logic (start/await) to the workflow once target CI platform is chosen.
- Collect logcat (`adb logcat -d`) for artifacts to aid post-run triage.
