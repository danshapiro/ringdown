import java.util.Locale
import java.util.Properties
import org.gradle.api.GradleException

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.kapt")
    id("com.google.dagger.hilt.android")
    id("org.jlleitschuh.gradle.ktlint")
}

val envFile = rootProject.projectDir.resolve("../config/.env").canonicalFile
if (!envFile.exists()) {
    throw GradleException(
        "Missing configuration file at ${envFile.path}. Copy android/config/.env.example and update the values."
    )
}

val envProperties = Properties().apply {
    envFile.inputStream().use { load(it) }
}

fun requireEnv(key: String): String =
    envProperties.getProperty(key)
        ?: throw GradleException("Missing '$key' in ${envFile.path}.")

fun requireBackendUrl(key: String): String {
    val value = requireEnv(key)
    if (!value.endsWith("/")) {
        throw GradleException("Backend URL for '$key' must end with '/'. Value: $value")
    }
    return value
}

fun requireBooleanEnv(key: String): Boolean =
    when (val value = requireEnv(key).lowercase(Locale.US)) {
        "true" -> true
        "false" -> false
        else -> throw GradleException("Invalid boolean for '$key': $value")
    }

fun requireIntEnv(key: String): Int =
    requireEnv(key).toIntOrNull()
        ?: throw GradleException("Invalid integer for '$key': ${requireEnv(key)}")

val stagingBackendUrl = requireBackendUrl("STAGING_BACKEND_BASE_URL")
val productionBackendUrl = requireBackendUrl("PRODUCTION_BACKEND_BASE_URL")
val debugUseRegistrationStub = requireBooleanEnv("DEBUG_USE_REGISTRATION_STUB")
val debugStubApprovalThreshold = requireIntEnv("DEBUG_STUB_APPROVAL_THRESHOLD")
val debugUseVoiceTransportStub = requireBooleanEnv("DEBUG_USE_VOICE_TRANSPORT_STUB")
val javaHome: String = System.getenv("JAVA_HOME")
    ?: System.getProperty("java.home")
    ?: throw GradleException("JAVA_HOME is not set. Configure the JDK before building.")

android {
    namespace = "com.ringdown"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.ringdown"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        multiDexEnabled = true
        multiDexKeepProguard = file("src/androidTest/multidex-keep.pro")
        vectorDrawables {
            useSupportLibrary = true
        }
    }

    buildTypes {
        getByName("debug") {
            buildConfigField("String", "BACKEND_BASE_URL", "\"$stagingBackendUrl\"")
            buildConfigField("boolean", "USE_STUB_REGISTRATION", debugUseRegistrationStub.toString())
            buildConfigField("int", "STUB_APPROVAL_THRESHOLD", debugStubApprovalThreshold.toString())
            buildConfigField("boolean", "USE_FAKE_VOICE_TRANSPORT", debugUseVoiceTransportStub.toString())
        }
        getByName("release") {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            buildConfigField("String", "BACKEND_BASE_URL", "\"$productionBackendUrl\"")
            buildConfigField("boolean", "USE_STUB_REGISTRATION", "false")
            buildConfigField("int", "STUB_APPROVAL_THRESHOLD", "0")
            buildConfigField("boolean", "USE_FAKE_VOICE_TRANSPORT", "false")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
        freeCompilerArgs = freeCompilerArgs + "-jdk-home=$javaHome"
    }
    buildFeatures {
        compose = true
        buildConfig = true
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
    implementation("org.jetbrains.kotlin:kotlin-stdlib:1.9.24")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.5")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.5")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.5")
    implementation("androidx.lifecycle:lifecycle-service:2.8.5")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.compose.animation:animation")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("com.google.android.material:material:1.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("androidx.datastore:datastore-preferences:1.1.1")

    // Networking & serialization
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-moshi:2.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("com.squareup.moshi:moshi-kotlin:1.15.1")
    implementation("androidx.media:media:1.7.0")
    implementation("io.github.webrtc-sdk:android:125.6422.07")

    // Dependency injection
    implementation("com.google.dagger:hilt-android:2.52")
    kapt("com.google.dagger:hilt-android-compiler:2.52")
    implementation("androidx.hilt:hilt-navigation-compose:1.2.0")
    implementation("com.google.accompanist:accompanist-permissions:0.36.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
    testImplementation("app.cash.turbine:turbine:1.1.0")

    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.5.1")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")

    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}

kapt {
    correctErrorTypes = true
}
