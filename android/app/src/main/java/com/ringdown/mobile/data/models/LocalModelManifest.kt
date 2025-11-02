package com.ringdown.mobile.data.models

import com.squareup.moshi.Json
import com.squareup.moshi.JsonClass

@JsonClass(generateAdapter = true)
data class LocalModelManifest(
    @Json(name = "schema_version") val schemaVersion: Int,
    @Json(name = "content_sha256") val contentSha256: String,
    val models: List<ModelEntry>,
) {

    @JsonClass(generateAdapter = true)
    data class ModelEntry(
        val id: String,
        @Json(name = "kind") val kind: String,
        @Json(name = "display_name") val displayName: String,
        @Json(name = "asset_dir") val assetDir: String,
        @Json(name = "install_dir") val installDir: String,
        val payloads: List<Payload>,
    )

    @JsonClass(generateAdapter = true)
    data class Payload(
        @Json(name = "relative_path") val relativePath: String,
        @Json(name = "sha256") val sha256: String,
        @Json(name = "size_bytes") val sizeBytes: Long,
    )
}
