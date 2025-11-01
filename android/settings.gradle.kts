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

rootProject.name = "RingdownAndroid"
include(":app")
include(":experiments:tts-demo")

project(":experiments:tts-demo").projectDir = File(rootDir, "experiments/tts-demo")
