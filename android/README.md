# Ringdown Android Client (Foundation)

This module contains the Jetpack Compose Android client described in `todo-android-spec/todo-android-spec.txt`. The current milestone covers the foundational registration flow, microphone permission gating, and idle UI scaffolding.

## Project layout

- `app/` – Main Android application module.
- `scripts/gradle.sh` – Wrapper helper for running Gradle tasks inside the Codex CLI environment.
- `scripts/init-project.sh` – Placeholder bootstrap script (kept for spec compatibility).

## Requirements

- JDK 17+
- Android SDK (API 35) – not required for unit tests, but needed for future instrumentation runs.

## Useful commands

```bash
# From the repo root
bash android/scripts/gradle.sh tasks
bash android/scripts/gradle.sh :app:assembleDebug
bash android/scripts/gradle.sh :app:testDebugUnitTest
```

> Note: The Codex CLI container does not currently ship with a JDK. Install or export `JAVA_HOME` before invoking the Gradle wrapper.
