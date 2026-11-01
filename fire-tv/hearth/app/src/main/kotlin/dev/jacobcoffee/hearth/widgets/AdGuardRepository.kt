package dev.jacobcoffee.hearth.widgets

import android.util.Log
import dev.jacobcoffee.hearth.net.NetworkClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

data class AdGuardSnapshot(
    val numDnsQueries: Long,
    val numBlockedFiltering: Long,
    val avgProcessingTimeMs: Double,
    val protectionEnabled: Boolean,
)

sealed class AdGuardState {
    object Loading : AdGuardState()
    data class Available(val snapshot: AdGuardSnapshot, val stale: Boolean) : AdGuardState()
    object Unavailable : AdGuardState()
}

@Serializable
private data class StatsResponse(
    @SerialName("num_dns_queries") val numDnsQueries: Long = 0,
    @SerialName("num_blocked_filtering") val numBlockedFiltering: Long = 0,
    @SerialName("avg_processing_time") val avgProcessingTime: Double = 0.0,
)

@Serializable
private data class StatusResponse(@SerialName("protection_enabled") val protectionEnabled: Boolean = false)

class AdGuardRepository {
    private val json = Json { ignoreUnknownKeys = true }
    @Volatile private var last: AdGuardSnapshot? = null

    suspend fun fetch(baseUrl: String, username: String, password: String): AdGuardState = withContext(Dispatchers.IO) {
        try {
            val base = baseUrl.trimEnd('/')
            val statsBody = NetworkClient.client.newCall(
                NetworkClient.get("$base/control/stats", username, password)
            ).execute().use { resp ->
                if (!resp.isSuccessful) throw java.io.IOException("HTTP ${resp.code}")
                resp.body?.string() ?: throw java.io.IOException("empty response")
            }
            val statusBody = NetworkClient.client.newCall(
                NetworkClient.get("$base/control/status", username, password)
            ).execute().use { resp ->
                if (!resp.isSuccessful) throw java.io.IOException("HTTP ${resp.code}")
                resp.body?.string() ?: throw java.io.IOException("empty response")
            }
            val stats = json.decodeFromString(StatsResponse.serializer(), statsBody)
            val status = json.decodeFromString(StatusResponse.serializer(), statusBody)
            // avg_processing_time is in seconds; the widget shows milliseconds.
            val snapshot = AdGuardSnapshot(
                numDnsQueries = stats.numDnsQueries,
                numBlockedFiltering = stats.numBlockedFiltering,
                avgProcessingTimeMs = stats.avgProcessingTime * 1000.0,
                protectionEnabled = status.protectionEnabled,
            )
            last = snapshot
            AdGuardState.Available(snapshot, stale = false)
        } catch (e: Exception) {
            Log.w("Hearth/AdGuard", "fetch failed: $e")
            val cached = last
            if (cached != null) AdGuardState.Available(cached, stale = true) else AdGuardState.Unavailable
        }
    }
}
