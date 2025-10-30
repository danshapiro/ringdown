plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.kapt")
    id("com.google.dagger.hilt.android")
}

import java.io.File
import java.util.Properties
import org.gradle.api.GradleException

fun loadEnvFile(path: java.io.File): Map<String, String> {
    if (!path.exists()) return emptyMap()
    return path.readLines()
        .map { it.trim() }
        .filter { it.isNotEmpty() && !it.startsWith("#") }
        .mapNotNull { line ->
            val idx = line.indexOf('=')
            if (idx <= 0) return@mapNotNull null
            val key = line.substring(0, idx).trim()
            val value = line.substring(idx + 1).trim()
            key to value
        }
        .toMap()
}

fun String.ensureTrailingSlash(): String = if (endsWith('/')) this else "$this/"

val localProperties = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.exists()) {
        file.inputStream().use { load(it) }
    }
}

val envConfig = loadEnvFile(rootProject.file("android/config/.env"))

fun stringConfig(key: String, default: String, ensureSlash: Boolean = false): String {
    val value = (localProperties.getProperty(key)
        ?: envConfig[key]
        ?: System.getenv(key)
        ?: default).trim()
    return if (ensureSlash && value.isNotEmpty()) value.ensureTrailingSlash() else value
}

fun booleanConfig(key: String, default: Boolean): Boolean {
    val raw = (localProperties.getProperty(key)
        ?: envConfig[key]
        ?: System.getenv(key))
    return raw?.trim()?.lowercase()?.let {
        when (it) {
            "true", "1", "yes" -> true
            "false", "0", "no" -> false
            else -> default
        }
    } ?: default
}

val stagingBackend = stringConfig("STAGING_BACKEND_BASE_URL", "https://staging.api.ringdown.ai/", ensureSlash = true)
val productionBackend = stringConfig("PRODUCTION_BACKEND_BASE_URL", "https://api.ringdown.ai/", ensureSlash = true)
val debugStubEnabled = booleanConfig("DEBUG_USE_REGISTRATION_STUB", true)

android {
    namespace = "com.ringdown.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.ringdown.mobile"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables {
            useSupportLibrary = true
        }

        testApplicationId = "com.ringdown.mobile.test"
        buildConfigField("String", "STAGING_BACKEND_BASE_URL", "\"$stagingBackend\"")
        buildConfigField("String", "PRODUCTION_BACKEND_BASE_URL", "\"$productionBackend\"")
        buildConfigField("Boolean", "DEBUG_USE_REGISTRATION_STUB", "${debugStubEnabled}")
        buildConfigField("int", "DEBUG_STUB_APPROVAL_THRESHOLD", "2")
        buildConfigField("Boolean", "ENABLE_TEST_CONTROL_HARNESS", "false")
    }

    buildTypes {
        getByName("debug") {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            buildConfigField("Boolean", "ENABLE_TEST_CONTROL_HARNESS", "true")
        }
        getByName("release") {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.14"
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    testOptions {
        unitTests.isIncludeAndroidResources = true
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.06.00")
    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.5")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.5")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3:1.3.0")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.runtime:runtime-livedata")
    implementation("androidx.navigation:navigation-compose:2.8.1")

    implementation("com.google.android.material:material:1.12.0")

    implementation("androidx.datastore:datastore-preferences:1.1.1")
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-moshi:2.11.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("co.daily:client:0.35.0")

    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.8.1")

    implementation("com.google.dagger:hilt-android:2.51.1")
    kapt("com.google.dagger:hilt-android-compiler:2.51.1")
    implementation("androidx.hilt:hilt-navigation-compose:1.2.0")

    implementation("com.squareup.moshi:moshi-kotlin:1.15.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("com.google.truth:truth:1.4.4")
    testImplementation("androidx.test:core:1.6.1")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
    testImplementation("app.cash.turbine:turbine:1.1.0")
    testImplementation("androidx.arch.core:core-testing:2.2.0")
    testImplementation("org.robolectric:robolectric:4.12.2")

    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("com.google.truth:truth:1.4.4")
    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}

kapt {
    correctErrorTypes = true
}

val adbExecutableProvider = providers.provider {
    val sdkDirCandidates = sequenceOf(
        localProperties.getProperty("sdk.dir"),
        System.getenv("ANDROID_SDK_ROOT"),
        System.getenv("ANDROID_HOME"),
    ).filterNotNull()
        .map { candidate -> File(candidate) }
        .firstOrNull()
        ?: throw GradleException(
            "Android SDK not configured. Set sdk.dir in local.properties or export ANDROID_SDK_ROOT.",
        )

    val osName = System.getProperty("os.name").lowercase()
    val isWindowsHost = osName.contains("windows")
    val isWsl = !isWindowsHost && System.getenv("WSL_DISTRO_NAME") != null
    val adbCandidates = if (isWindowsHost || isWsl) {
        listOf("adb.exe", "adb")
    } else {
        listOf("adb")
    }

    val adbFile = adbCandidates
        .asSequence()
        .map { candidate -> sdkDirCandidates.resolve("platform-tools").resolve(candidate) }
        .firstOrNull { it.exists() }
        ?: throw GradleException(
            "adb executable not found in ${sdkDirCandidates.resolve("platform-tools")}. Install platform-tools with sdkmanager.",
        )
    adbFile.absolutePath
}

tasks.register("connectedVoiceMvpAndroidTest") {
    group = "verification"
    description = "Installs debug + androidTest APKs and runs realtime voice instrumentation via adb."

    val debugApk = layout.buildDirectory.file("outputs/apk/debug/app-debug.apk")
    val androidTestApk = layout.buildDirectory.file("outputs/apk/androidTest/debug/app-debug-androidTest.apk")

    inputs.files(debugApk, androidTestApk)
    outputs.upToDateWhen { false }

    dependsOn("assembleDebug", "assembleDebugAndroidTest")

    doLast {
        val adbExecutable = adbExecutableProvider.get()

        val serialOverride = project.findProperty("android.deviceSerial")?.toString()?.takeIf { it.isNotBlank() }
        val androidSerial = serialOverride ?: System.getenv("ANDROID_SERIAL")?.takeIf { it.isNotBlank() }

        val adbEnv = mutableMapOf<String, String>()
        val adbServerPortProperty = project.findProperty("adb.server.port")?.toString()?.takeIf { it.isNotBlank() }
        val adbServerPortEnv = sequenceOf(
            System.getenv("ANDROID_ADB_SERVER_PORT"),
            System.getenv("ADB_SERVER_PORT"),
        ).firstOrNull { !it.isNullOrBlank() }

        adbServerPortProperty?.let { adbEnv["ANDROID_ADB_SERVER_PORT"] = it }
        if (!adbEnv.containsKey("ANDROID_ADB_SERVER_PORT") && !adbServerPortEnv.isNullOrBlank()) {
            adbEnv["ANDROID_ADB_SERVER_PORT"] = adbServerPortEnv
        }

        val adbServerHostProperty = project.findProperty("adb.server.host")?.toString()?.takeIf { it.isNotBlank() }
        val adbServerSocketEnv = System.getenv("ADB_SERVER_SOCKET")?.takeIf { it.isNotBlank() }
        if (adbServerHostProperty != null) {
            val portForSocket = adbEnv["ANDROID_ADB_SERVER_PORT"] ?: adbServerPortEnv ?: "5037"
            adbEnv["ADB_SERVER_SOCKET"] = "tcp:$adbServerHostProperty:$portForSocket"
        } else if (adbServerSocketEnv != null) {
            adbEnv["ADB_SERVER_SOCKET"] = adbServerSocketEnv
        }

        fun buildAdbCommand(vararg args: String): List<String> {
            val command = mutableListOf(adbExecutable)
            if (androidSerial != null) {
                command += listOf("-s", androidSerial)
            }
            command += args
            return command
        }

        fun execAdb(vararg args: String) {
            logger.lifecycle("adb {}", args.joinToString(" "))
            project.exec {
                commandLine(buildAdbCommand(*args))
                if (adbEnv.isNotEmpty()) {
                    environment(adbEnv as Map<String, String>)
                }
            }
        }

        fun File.toAdbPath(): String {
            val rawPath = absolutePath
            if (!adbExecutable.endsWith(".exe", ignoreCase = true)) {
                return rawPath
            }
            if (!rawPath.startsWith("/mnt/") || rawPath.length < 7) {
                return rawPath
            }
            val driveLetter = rawPath[5]
            if (!driveLetter.isLetter()) {
                return rawPath
            }
            val remainder = rawPath.substring(6).replace('/', '\\')
            return "${driveLetter.uppercaseChar()}:\\${remainder}"
        }

        val debugApkPath = debugApk.get().asFile.toAdbPath()
        val androidTestApkPath = androidTestApk.get().asFile.toAdbPath()
        execAdb("install", "-r", debugApkPath)
        execAdb("install", "-r", "-t", androidTestApkPath)
        execAdb("shell", "am", "force-stop", "${android.defaultConfig.applicationId}.debug")

        val instrumentationArgPrefix = "android.testInstrumentationRunnerArguments."
        val instrumentationArgs = project.properties
            .filterKeys { it.startsWith(instrumentationArgPrefix) }
            .map { (rawKey, value) ->
                rawKey.removePrefix(instrumentationArgPrefix) to value.toString()
            }
            .sortedBy { it.first }

        val instrumentationCommand = mutableListOf("shell", "am", "instrument", "-w", "-r")
        instrumentationArgs.forEach { (name, value) ->
            instrumentationCommand += listOf("-e", name, value)
        }
        instrumentationCommand += "com.ringdown.mobile.test/androidx.test.runner.AndroidJUnitRunner"

        execAdb(*instrumentationCommand.toTypedArray())
    }
}
