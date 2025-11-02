package com.ringdown.mobile.data.models

import android.content.Context
import android.content.res.AssetManager
import androidx.annotation.VisibleForTesting
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.locks.ReentrantLock
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.concurrent.withLock

@Singleton
class LocalModelInstaller @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    private val lock = ReentrantLock()
    private val assets: AssetManager = context.assets
    private val rootDir: File = File(context.filesDir, ROOT_FOLDER)

    fun ensurePiperModel(): File = lock.withLock {
        installDirectoryIfMissing(
            assetDir = PIPER_ASSET_DIR,
            sentinelName = PIPER_SENTINEL,
        )
    }

    private fun installDirectoryIfMissing(
        assetDir: String,
        sentinelName: String,
    ): File {
        val targetDir = File(rootDir, assetDir)
        val sentinel = File(targetDir, sentinelName)
        if (sentinel.exists()) {
            return targetDir
        }

        if (targetDir.exists()) {
            targetDir.deleteRecursively()
        }
        copyAssetDirectory(assetDir, targetDir)
        return targetDir
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

    @VisibleForTesting
    fun clearAllInstalledModels() {
        lock.withLock {
            if (rootDir.exists()) {
                rootDir.deleteRecursively()
            }
        }
    }

    companion object {
        private const val ROOT_FOLDER = "models"
        private const val PIPER_ASSET_DIR = "vits-piper-en_US-amy-low"
        private const val PIPER_SENTINEL = "en_US-amy-low.onnx"
    }
}
