package com.ringdown.mobile.data.models

import com.google.common.truth.Truth.assertThat
import java.nio.file.Files
import org.junit.Test

class InstalledModelMetadataTest {

    @Test
    fun matchesReturnsTrueWhenStateIsValid() {
        val tempDir = Files.createTempDirectory("model-test").toFile()
        try {
            val payloadFile = tempDir.resolve("foo.bin")
            payloadFile.outputStream().use { output ->
                output.write(ByteArray(4) { 0x01 })
            }
            val entry = LocalModelManifest.ModelEntry(
                id = "test-model",
                kind = "tts",
                displayName = "Test Model",
                assetDir = "assets/test-model",
                installDir = "test-model",
                payloads = listOf(
                    LocalModelManifest.Payload(
                        relativePath = "foo.bin",
                        sha256 = "abcd",
                        sizeBytes = 4,
                    ),
                ),
            )
            val manifest = LocalModelManifest(
                schemaVersion = 1,
                contentSha256 = "digest",
                models = listOf(entry),
            )
            val metadata = LocalModelInstaller.InstalledModelMetadata(
                modelId = "test-model",
                manifestContentSha256 = "digest",
                payloads = listOf(
                    LocalModelInstaller.InstalledPayload(
                        relativePath = "foo.bin",
                        sha256 = "abcd",
                        sizeBytes = 4,
                    ),
                ),
            )

            assertThat(metadata.matches(entry, manifest, tempDir)).isTrue()
        } finally {
            tempDir.deleteRecursively()
        }
    }

    @Test
    fun matchesReturnsFalseWhenFileMissing() {
        val tempDir = Files.createTempDirectory("model-test-missing").toFile()
        try {
            val entry = LocalModelManifest.ModelEntry(
                id = "test-model",
                kind = "tts",
                displayName = "Test Model",
                assetDir = "assets/test-model",
                installDir = "test-model",
                payloads = listOf(
                    LocalModelManifest.Payload(
                        relativePath = "foo.bin",
                        sha256 = "abcd",
                        sizeBytes = 4,
                    ),
                ),
            )
            val manifest = LocalModelManifest(
                schemaVersion = 1,
                contentSha256 = "digest",
                models = listOf(entry),
            )
            val metadata = LocalModelInstaller.InstalledModelMetadata(
                modelId = "test-model",
                manifestContentSha256 = "digest",
                payloads = listOf(
                    LocalModelInstaller.InstalledPayload(
                        relativePath = "foo.bin",
                        sha256 = "abcd",
                        sizeBytes = 4,
                    ),
                ),
            )

            assertThat(metadata.matches(entry, manifest, tempDir)).isFalse()
        } finally {
            tempDir.deleteRecursively()
        }
    }
}
