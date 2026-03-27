package com.ringdown.mobile.data.models

import android.content.Context
import android.content.res.AssetManager
import androidx.annotation.VisibleForTesting
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.locks.ReentrantLock
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.concurrent.withLock
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Singleton
class LocalModelInstaller @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    enum class LocalModelId(val manifestId: String) {
        PIPER_AMY_LOW("piper-amy-low-tts"),
        SHERPA_STREAMING_EN_20M("sherpa-streaming-zipformer-en-20m"),
    }

    private val lock = ReentrantLock()
    private val assets: AssetManager = context.assets
    private val rootDir: File = File(context.filesDir, ROOT_FOLDER)
    private val moshi: Moshi = Moshi.Builder()
        .add(KotlinJsonAdapterFactory())
        .build()
    private val manifestAdapter = moshi.adapter(LocalModelManifest::class.java)
    private val metadataAdapter = moshi.adapter(InstalledModelMetadata::class.java)

    private val manifest: LocalModelManifest by lazy { loadManifest() }
    private val modelsById: Map<String, LocalModelManifest.ModelEntry> by lazy {
        manifest.models.associateBy { it.id }
    }

    suspend fun ensureModel(modelId: LocalModelId): File = withContext(Dispatchers.IO) {
        lock.withLock {
            ensureRootDirectory()
            val entry = modelsById[modelId.manifestId]
                ?: throw IllegalArgumentException("Unknown model id: ${modelId.manifestId}")
            ensureInstalled(entry)
        }
    }

    suspend fun ensurePiperModel(): File = ensureModel(LocalModelId.PIPER_AMY_LOW)

    suspend fun ensureSherpaStreamingAsrModel(): File = ensureModel(LocalModelId.SHERPA_STREAMING_EN_20M)

    private fun ensureInstalled(entry: LocalModelManifest.ModelEntry): File {
        val installDir = File(rootDir, entry.installDir)
        val metadata = installDir.metadataOrNull()
        if (metadata != null && metadata.matches(entry, manifest, installDir)) {
            return installDir
        }
        return reinstall(entry, installDir)
    }

    private fun reinstall(entry: LocalModelManifest.ModelEntry, installDir: File): File {
        val tempDirName = "${entry.installDir}.tmp-${UUID.randomUUID()}"
        val tempDir = File(rootDir, tempDirName)
        if (tempDir.exists()) {
            tempDir.deleteRecursively()
        }
        tempDir.mkdirs()

        try {
            copyAssetDirectory(entry.assetDir, tempDir)
            validatePayloads(entry, tempDir)
            if (installDir.exists()) {
                installDir.deleteRecursively()
            }
            if (!tempDir.renameTo(installDir)) {
                tempDir.copyRecursively(target = installDir, overwrite = true)
                tempDir.deleteRecursively()
            }
            writeMetadata(entry, installDir)
        } finally {
            if (tempDir.exists()) {
                tempDir.deleteRecursively()
            }
        }
        return installDir
    }

    private fun validatePayloads(entry: LocalModelManifest.ModelEntry, baseDir: File) {
        entry.payloads.forEach { payload ->
            val file = File(baseDir, payload.relativePath)
            if (!file.exists()) {
                throw IOException("Missing payload ${payload.relativePath} for ${entry.id}")
            }
            if (file.length() != payload.sizeBytes) {
                throw IOException(
                    "Size mismatch for ${payload.relativePath}: expected ${payload.sizeBytes}, actual ${file.length()}",
                )
            }
            val actualHash = computeSha256(file)
            if (!actualHash.equals(payload.sha256, ignoreCase = true)) {
                throw IOException(
                    "Checksum mismatch for ${payload.relativePath}: expected ${payload.sha256}, actual $actualHash",
                )
            }
        }
    }

    private fun File.metadataOrNull(): InstalledModelMetadata? {
        if (!exists()) return null
        val metadataFile = File(this, INSTALLED_METADATA)
        if (!metadataFile.exists()) return null
        return runCatching { metadataAdapter.fromJson(metadataFile.readText()) }.getOrNull()
    }

    private fun writeMetadata(entry: LocalModelManifest.ModelEntry, installDir: File) {
        val metadata = InstalledModelMetadata(
            modelId = entry.id,
            manifestContentSha256 = manifest.contentSha256,
            payloads = entry.payloads.map {
                InstalledPayload(
                    relativePath = it.relativePath,
                    sha256 = it.sha256,
                    sizeBytes = it.sizeBytes,
                )
            },
        )
        val metadataFile = File(installDir, INSTALLED_METADATA)
        metadataFile.parentFile?.mkdirs()
        metadataFile.writeText(metadataAdapter.toJson(metadata))
    }

    private fun copyAssetDirectory(assetPath: String, destDir: File) {
        val normalizedPath = assetPath.trim('/')
        val children = assets.list(normalizedPath) ?: emptyArray()
        if (children.isEmpty()) {
            copyAssetFile(normalizedPath, destDir)
            return
        }

        if (!destDir.exists()) {
            destDir.mkdirs()
        }

        for (child in children) {
            if (child.isBlank()) continue
            val childAssetPath = joinAssetPath(normalizedPath, child)
            val childDest = File(destDir, child)
            val grandChildren = assets.list(childAssetPath) ?: emptyArray()
            if (grandChildren.isEmpty()) {
                copyAssetFile(childAssetPath, childDest)
            } else {
                copyAssetDirectory(childAssetPath, childDest)
            }
        }
    }

    private fun copyAssetFile(assetPath: String, destFile: File) {
        destFile.parentFile?.mkdirs()
        val normalizedPath = assetPath.trim('/')
        if (normalizedPath.isEmpty()) return
        assets.open(normalizedPath).use { input ->
            FileOutputStream(destFile).use { output ->
                input.copyTo(output)
            }
        }
    }

    private fun joinAssetPath(parent: String, child: String): String {
        val trimmedParent = parent.trim('/')
        val trimmedChild = child.trim('/')
        return when {
            trimmedParent.isEmpty() -> trimmedChild
            trimmedChild.isEmpty() -> trimmedParent
            else -> "$trimmedParent/$trimmedChild"
        }
    }

    private fun ensureRootDirectory() {
        if (!rootDir.exists()) {
            rootDir.mkdirs()
        }
    }

    private fun loadManifest(): LocalModelManifest {
        assets.open(MODEL_MANIFEST_ASSET).use { stream ->
            val json = stream.bufferedReader().use { it.readText() }
            val manifest = manifestAdapter.fromJson(json)
                ?: throw IllegalStateException("Failed to parse $MODEL_MANIFEST_ASSET")
            if (manifest.schemaVersion != 1) {
                throw IllegalStateException("Unsupported manifest schema version ${manifest.schemaVersion}")
            }
            return manifest
        }
    }

    private fun computeSha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
            while (true) {
                val read = input.read(buffer)
                if (read <= 0) {
                    break
                }
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString(separator = "") { byte ->
            "%02x".format(byte)
        }
    }

    @VisibleForTesting
    fun clearAllInstalledModels() {
        lock.withLock {
            if (rootDir.exists()) {
                rootDir.deleteRecursively()
            }
        }
    }

    @VisibleForTesting
    fun installedMetadata(modelId: LocalModelId): InstalledModelMetadata? {
        val entry = modelsById[modelId.manifestId] ?: return null
        val installDir = File(rootDir, entry.installDir)
        return installDir.metadataOrNull()
    }

    data class InstalledModelMetadata(
        val modelId: String,
        val manifestContentSha256: String,
        val payloads: List<InstalledPayload>,
    ) {
        fun matches(
            entry: LocalModelManifest.ModelEntry,
            manifest: LocalModelManifest,
            installDir: File,
        ): Boolean {
            if (modelId != entry.id) return false
            if (manifestContentSha256 != manifest.contentSha256) return false
            val expected = entry.payloads.associateBy { it.relativePath }
            val actual = payloads.associateBy { it.relativePath }
            if (expected.keys != actual.keys) return false
            for ((path, expectedPayload) in expected) {
                val recorded = actual[path] ?: return false
                if (!recorded.sha256.equals(expectedPayload.sha256, ignoreCase = true)) return false
                if (recorded.sizeBytes != expectedPayload.sizeBytes) return false
                val file = File(installDir, path)
                if (!file.exists() || file.length() != expectedPayload.sizeBytes) return false
            }
            return true
        }
    }

    data class InstalledPayload(
        val relativePath: String,
        val sha256: String,
        val sizeBytes: Long,
    )

    companion object {
        private const val ROOT_FOLDER = "models"
        private const val MODEL_MANIFEST_ASSET = "model_manifest.json"
        private const val INSTALLED_METADATA = ".installed_manifest.json"
    }
}
