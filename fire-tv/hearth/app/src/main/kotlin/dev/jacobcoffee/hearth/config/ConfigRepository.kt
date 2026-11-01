package dev.jacobcoffee.hearth.config

import android.content.Context
import kotlinx.serialization.json.Json
import java.io.File

/** Reads/writes the config pushed via ConfigProvider, stored app-private at files/config.json. */
class ConfigRepository(context: Context) {
    private val appContext = context.applicationContext
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val file = File(appContext.filesDir, "config.json")

    fun load(): HearthConfig {
        if (!file.isFile) return HearthConfig()
        return runCatching { json.decodeFromString(HearthConfig.serializer(), file.readText()) }
            .getOrDefault(HearthConfig())
    }

    /** @return true if the JSON parsed and validated; the file is only written on success. */
    fun save(rawJson: String): Boolean {
        val parsed = runCatching { json.decodeFromString(HearthConfig.serializer(), rawJson) }.getOrNull()
            ?: return false
        file.writeText(json.encodeToString(HearthConfig.serializer(), parsed))
        return true
    }
}
