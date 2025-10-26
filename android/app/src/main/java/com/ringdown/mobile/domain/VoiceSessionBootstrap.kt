package com.ringdown.mobile.domain

data class VoiceSessionBootstrap(
    val clientSecret: String,
    val model: String,
    val voice: String?,
    val transcriptsChannel: String,
    val controlChannel: String,
    val iceServers: List<IceServerConfig>,
    val turnDetection: Map<String, Any>?,
)

data class IceServerConfig(
    val urls: List<String>,
    val username: String?,
    val credential: String?,
)
