#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: init-project.sh [--app-id <package>] [--app-name <name>] [--gradle-version <version>]

Scaffold the Android client project under android/app with a Jetpack Compose single-activity
template, Gradle wrapper, ktlint configuration, and CI workflow stub. The script is idempotent
when the target directory does not yet exist; it will refuse to overwrite an existing project.
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

require_command() {
  local cmd=$1
  command -v "$cmd" >/dev/null 2>&1 || die "Required command '$cmd' not found in PATH"
}

apply_template() {
  local file_path=$1
  shift || true
  mkdir -p "$(dirname "$file_path")"
  cat >"$file_path"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$ANDROID_ROOT/app"
MANAGED_JAVA_HOME="$ANDROID_ROOT/.jdk/current"

if [[ -z "${JAVA_HOME:-}" && -d "$MANAGED_JAVA_HOME" ]]; then
  export JAVA_HOME="$MANAGED_JAVA_HOME"
  export PATH="$JAVA_HOME/bin:$PATH"
fi

APP_ID="com.ringdown"
APP_NAME="Ringdown"
GRADLE_VERSION="8.7"
FORCE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-id)
      [[ $# -ge 2 ]] || die "Missing value for --app-id"
      APP_ID="$2"
      shift 2
      ;;
    --app-name)
      [[ $# -ge 2 ]] || die "Missing value for --app-name"
      APP_NAME="$2"
      shift 2
      ;;
    --gradle-version)
      [[ $# -ge 2 ]] || die "Missing value for --gradle-version"
      GRADLE_VERSION="$2"
      shift 2
      ;;
    --force)
      FORCE=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

if [[ -d "$PROJECT_ROOT/app" && "$FORCE" != true ]]; then
  die "Android project already exists at $PROJECT_ROOT. Re-run with --force to overwrite."
fi

mkdir -p "$PROJECT_ROOT"

require_command curl
require_command unzip
require_command tar

PACKAGE_DIR="${APP_ID//./\/}"

cat <<EOF | apply_template "$PROJECT_ROOT/settings.gradle.kts"
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "ringdown-android"
include(":app")
EOF

cat <<EOF | apply_template "$PROJECT_ROOT/build.gradle.kts"
plugins {
    id("com.android.application") version "8.6.0" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
    id("org.jlleitschuh.gradle.ktlint") version "12.1.1" apply false
}
EOF

cat <<EOF | apply_template "$PROJECT_ROOT/gradle.properties"
org.gradle.jvmargs=-Xmx4g -Dfile.encoding=UTF-8
android.useAndroidX=true
android.nonTransitiveRClass=true
android.nonFinalResIds=true
kotlin.code.style=official
org.gradle.caching=true
org.gradle.parallel=true
android.experimental.enableNewResourceShrinker=true
EOF

cat <<EOF | apply_template "$PROJECT_ROOT/.gitignore"
.gradle/
build/
local.properties
*.iml
.idea/
captures/
.kotlin/
*.keystore
EOF

cat <<'EOF' | apply_template "$PROJECT_ROOT/.editorconfig"
root = true

[*]
indent_style = space
indent_size = 4
insert_final_newline = true
charset = utf-8

[*.{kt,kts}]
indent_size = 4
max_line_length = 120
EOF

cat <<'EOF' | apply_template "$PROJECT_ROOT/.github/workflows/android-ci.yml"
name: Android CI

on:
  push:
    paths:
      - 'android/app/**'
  pull_request:
    paths:
      - 'android/app/**'

jobs:
  build:
    runs-on: ubuntu-latest
    env:
      ANDROID_SDK_ROOT: ${{ github.workspace }}/.android-sdk
    steps:
      - uses: actions/checkout@v4
      - name: Set up JDK
        uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: 21
      - name: Cache Gradle
        uses: actions/cache@v4
        with:
          path: |
            ~/.gradle/caches
            ~/.gradle/wrapper
          key: gradle-${{ runner.os }}-${{ hashFiles('android/app/**') }}
          restore-keys: |
            gradle-${{ runner.os }}-
      - name: Grant execute permission
        run: chmod +x android/app/gradlew
      - name: Run checks
        run: bash android/scripts/gradle.sh ./gradlew :app:lintDebug :app:testDebugUnitTest
EOF

cat <<EOF | apply_template "$PROJECT_ROOT/app/build.gradle.kts"
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jlleitschuh.gradle.ktlint")
}

android {
    namespace = "$APP_ID"
    compileSdk = 35

    defaultConfig {
        applicationId = "$APP_ID"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables {
            useSupportLibrary = true
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
    }
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.14"
    }
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.01")
    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.5")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("com.google.android.material:material:1.12.0")

    testImplementation("junit:junit:4.13.2")

    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.5.1")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")

    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
EOF

cat <<'EOF' | apply_template "$PROJECT_ROOT/app/proguard-rules.pro"
# Placeholder for future release optimizations.
-dontwarn
EOF

cat <<EOF | apply_template "$PROJECT_ROOT/app/src/main/AndroidManifest.xml"
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <application
        android:allowBackup="true"
        android:dataExtractionRules="@xml/data_extraction_rules"
        android:fullBackupContent="@xml/backup_rules"
        android:icon="@mipmap/ic_launcher"
        android:label="$APP_NAME"
        android:roundIcon="@mipmap/ic_launcher_round"
        android:supportsRtl="true"
        android:theme="@style/Theme.Ringdown">
        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:label="$APP_NAME">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />

                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>

</manifest>
EOF

cat <<EOF | apply_template "$PROJECT_ROOT/app/src/main/java/$PACKAGE_DIR/MainActivity.kt"
package $APP_ID

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.Button
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import $APP_ID.ui.theme.RingdownTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            RingdownTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    PendingApprovalPlaceholder(
                        onCheckAgain = { /* TODO: wire backend registration */ }
                    )
                }
            }
        }
    }
}

@Composable
fun PendingApprovalPlaceholder(onCheckAgain: () -> Unit, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxSize(),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(
            text = "Device pending approval.",
            style = MaterialTheme.typography.headlineSmall,
            textAlign = TextAlign.Center
        )
        Button(onClick = onCheckAgain) {
            Text(text = "Check again")
        }
    }
}

@Preview(showBackground = true)
@Composable
fun PendingApprovalPreview() {
    RingdownTheme {
        PendingApprovalPlaceholder(onCheckAgain = {})
    }
}
EOF

cat <<EOF | apply_template "$PROJECT_ROOT/app/src/main/java/$PACKAGE_DIR/ui/theme/Color.kt"
package $APP_ID.ui.theme

import androidx.compose.ui.graphics.Color

val Purple80 = Color(0xFFD0BCFF)
val PurpleGrey80 = Color(0xFFCCC2DC)
val Pink80 = Color(0xFFEFB8C8)

val Purple40 = Color(0xFF6650a4)
val PurpleGrey40 = Color(0xFF625b71)
val Pink40 = Color(0xFF7D5260)
EOF

cat <<EOF | apply_template "$PROJECT_ROOT/app/src/main/java/$PACKAGE_DIR/ui/theme/Theme.kt"
package $APP_ID.ui.theme

import android.app.Activity
import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

private val DarkColorScheme = darkColorScheme(
    primary = Purple80,
    secondary = PurpleGrey80,
    tertiary = Pink80
)

private val LightColorScheme = lightColorScheme(
    primary = Purple40,
    secondary = PurpleGrey40,
    tertiary = Pink40
)

@Composable
fun RingdownTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    dynamicColor: Boolean = true,
    content: @Composable () -> Unit
) {
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val context = LocalContext.current
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        }
        darkTheme -> DarkColorScheme
        else -> LightColorScheme
    }

    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = colorScheme.primary.toArgb()
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = Typography,
        content = content
    )
}
EOF

cat <<EOF | apply_template "$PROJECT_ROOT/app/src/main/java/$PACKAGE_DIR/ui/theme/Type.kt"
package $APP_ID.ui.theme

import androidx.compose.material3.Typography

val Typography = Typography()
EOF

cat <<'EOF' | apply_template "$PROJECT_ROOT/app/src/main/res/values/colors.xml"
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="purple_200">#FFBB86FC</color>
    <color name="purple_500">#FF6200EE</color>
    <color name="purple_700">#FF3700B3</color>
    <color name="teal_200">#FF03DAC5</color>
    <color name="teal_700">#FF018786</color>
    <color name="black">#FF000000</color>
    <color name="white">#FFFFFFFF</color>
    <color name="ic_launcher_background">#121212</color>
</resources>
EOF

cat <<EOF | apply_template "$PROJECT_ROOT/app/src/main/res/values/strings.xml"
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">$APP_NAME</string>
</resources>
EOF

cat <<'EOF' | apply_template "$PROJECT_ROOT/app/src/main/res/values/themes.xml"
<?xml version="1.0" encoding="utf-8"?>
<resources>

    <style name="Theme.Ringdown" parent="Theme.Material3.DayNight.NoActionBar">
        <item name="android:statusBarColor">@android:color/transparent</item>
    </style>

</resources>
EOF

cat <<'EOF' | apply_template "$PROJECT_ROOT/app/src/main/res/values-night/themes.xml"
<?xml version="1.0" encoding="utf-8"?>
<resources>

    <style name="Theme.Ringdown" parent="Theme.Material3.DayNight.NoActionBar">
        <item name="android:statusBarColor">@android:color/transparent</item>
    </style>

</resources>
EOF

cat <<'EOF' | apply_template "$PROJECT_ROOT/app/src/main/res/xml/data_extraction_rules.xml"
<?xml version="1.0" encoding="utf-8"?>
<data-extraction-rules>
    <cloud-backup>
        <include domain="file" path="." />
    </cloud-backup>
    <device-transfer>
        <include domain="file" path="." />
    </device-transfer>
</data-extraction-rules>
EOF

cat <<'EOF' | apply_template "$PROJECT_ROOT/app/src/main/res/xml/backup_rules.xml"
<?xml version="1.0" encoding="utf-8"?>
<full-backup-content>
    <include domain="file" path="." />
</full-backup-content>
EOF

cat <<'EOF' | apply_template "$PROJECT_ROOT/app/src/main/res/drawable/ic_launcher_foreground.xml"
<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="108"
    android:viewportHeight="108">
    <path
        android:fillColor="#FFFFFF"
        android:pathData="M54,18A36,36 0 1,0 90,54 36,36 0 0,0 54,18ZM54,84A30,30 0 1,1 84,54 30,30 0 0,1 54,84ZM42,42H66V66H42Z" />
</vector>
EOF

cat <<'EOF' | apply_template "$PROJECT_ROOT/app/src/main/res/drawable/ic_launcher_background.xml"
<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">
    <solid android:color="@color/ic_launcher_background" />
</shape>
EOF

cat <<'EOF' | apply_template "$PROJECT_ROOT/app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml"
<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />
</adaptive-icon>
EOF

cat <<'EOF' | apply_template "$PROJECT_ROOT/app/src/main/res/mipmap-anydpi-v26/ic_launcher_round.xml"
<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />
</adaptive-icon>
EOF

cat <<EOF | apply_template "$PROJECT_ROOT/app/src/test/java/$PACKAGE_DIR/ExampleUnitTest.kt"
package $APP_ID

import org.junit.Assert.assertEquals
import org.junit.Test

class ExampleUnitTest {
    @Test
    fun addition_isCorrect() {
        assertEquals(4, 2 + 2)
    }
}
EOF

cat <<EOF | apply_template "$PROJECT_ROOT/app/src/androidTest/java/$PACKAGE_DIR/ExampleInstrumentedTest.kt"
package $APP_ID

import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ExampleInstrumentedTest {
    @Test
    fun useAppContext() {
        val appContext = InstrumentationRegistry.getInstrumentation().targetContext
        assertEquals("$APP_ID", appContext.packageName)
    }
}
EOF

download_gradle_wrapper() {
  local distribution="https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip"
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  echo "Downloading Gradle ${GRADLE_VERSION}..."
  curl -fsSL "$distribution" -o "$tmpdir/gradle.zip"
  unzip -q "$tmpdir/gradle.zip" -d "$tmpdir"

  local gradle_home
  gradle_home=$(find "$tmpdir" -maxdepth 1 -type d -name "gradle-${GRADLE_VERSION}" -print -quit)
  [[ -n "$gradle_home" ]] || die "Failed to extract Gradle ${GRADLE_VERSION}"

  echo "Generating Gradle wrapper..."
  "$gradle_home/bin/gradle" -p "$PROJECT_ROOT" wrapper --gradle-version "$GRADLE_VERSION" --distribution-type bin

  trap - RETURN
}

download_gradle_wrapper

chmod +x "$PROJECT_ROOT/gradlew"

echo "Android project scaffolded at $PROJECT_ROOT"
