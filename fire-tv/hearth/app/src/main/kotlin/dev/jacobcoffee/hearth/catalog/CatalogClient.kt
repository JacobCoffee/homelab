package dev.jacobcoffee.hearth.catalog

import android.content.Context
import android.util.Log
import dev.jacobcoffee.hearth.net.NetworkClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import java.io.File
import java.net.URL

sealed class CatalogFetchResult {
    data class Success(val index: CatalogIndex, val baseUrl: String) : CatalogFetchResult()
    data class Failure(val reason: String) : CatalogFetchResult()
}

sealed class DownloadResult {
    data class Success(val id: String, val version: Int) : DownloadResult()
    data class Failure(val reason: String) : DownloadResult()
}

/** Fetches the remote theme catalog, downloads entries, and installs them via [CatalogInstaller]. */
class CatalogClient(private val context: Context) {
    private val json = Json { ignoreUnknownKeys = true }
    private val themesDir: File get() = File(context.filesDir, "themes")

    suspend fun fetchIndex(catalogUrl: String): CatalogFetchResult = withContext(Dispatchers.IO) {
        try {
            val body = NetworkClient.client.newCall(NetworkClient.get(catalogUrl)).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext CatalogFetchResult.Failure("HTTP ${resp.code}")
                resp.body?.string() ?: return@withContext CatalogFetchResult.Failure("empty response")
            }
            val index = json.decodeFromString(CatalogIndex.serializer(), body)
            val baseUrl = catalogUrl.substringBeforeLast('/', missingDelimiterValue = "")
            CatalogFetchResult.Success(index, baseUrl)
        } catch (e: Exception) {
            Log.w("Hearth/Catalog", "fetchIndex failed: $e")
            CatalogFetchResult.Failure(e.message ?: e.toString())
        }
    }

    fun resolveUrl(baseUrl: String, relative: String): String =
        if (relative.startsWith("http://") || relative.startsWith("https://")) relative
        else URL(URL("$baseUrl/"), relative).toString()

    suspend fun download(entry: CatalogThemeEntry, baseUrl: String): DownloadResult = withContext(Dispatchers.IO) {
        try {
            val url = resolveUrl(baseUrl, entry.url)
            val bytes = NetworkClient.client.newCall(NetworkClient.get(url)).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext DownloadResult.Failure("HTTP ${resp.code}")
                resp.body?.bytes() ?: return@withContext DownloadResult.Failure("empty response")
            }
            when (val outcome = CatalogInstaller.install(bytes, entry.sha256, entry.size, entry.id, themesDir)) {
                is InstallOutcome.Installed -> DownloadResult.Success(outcome.id, outcome.version)
                is InstallOutcome.Rejected -> DownloadResult.Failure(outcome.reason)
            }
        } catch (e: Exception) {
            Log.w("Hearth/Catalog", "download failed for ${entry.id}: $e")
            DownloadResult.Failure(e.message ?: e.toString())
        }
    }

    fun downloadedThemeIds(): Set<String> =
        themesDir.listFiles { f -> f.isDirectory && !f.name.startsWith(".") }
            ?.map { it.name }?.toSet() ?: emptySet()

    fun delete(id: String): Boolean {
        val dir = File(themesDir, id)
        return !dir.exists() || dir.deleteRecursively()
    }
}
