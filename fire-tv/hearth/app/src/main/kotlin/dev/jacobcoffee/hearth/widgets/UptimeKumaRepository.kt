package dev.jacobcoffee.hearth.widgets

import android.util.Log
import dev.jacobcoffee.hearth.net.NetworkClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatterBuilder
import java.time.temporal.ChronoField

/** One monitor's latest state. [since] is when that state began, as far back as Kuma's beat history goes. */
data class KumaMonitor(val name: String, val status: Int, val since: Instant?, val pingMs: Double?, val sinceIsLowerBound: Boolean)

data class KumaSnapshot(val upCount: Int, val downCount: Int, val downNames: List<String>, val monitors: List<KumaMonitor>)

sealed class KumaState {
    object Loading : KumaState()
    data class Available(val snapshot: KumaSnapshot, val stale: Boolean) : KumaState()
    object Unavailable : KumaState()
}

@Serializable
private data class StatusPageResponse(val publicGroupList: List<MonitorGroup> = emptyList())

@Serializable
private data class MonitorGroup(val monitorList: List<MonitorRef> = emptyList())

@Serializable
private data class MonitorRef(val id: Int, val name: String)

@Serializable
private data class Heartbeat(val status: Int, val time: String, val msg: String? = null, val ping: Double? = null)

@Serializable
private data class HeartbeatResponse(val heartbeatList: Map<String, List<Heartbeat>> = emptyMap())

class UptimeKumaRepository {
    private val json = Json { ignoreUnknownKeys = true }
    @Volatile private var last: KumaSnapshot? = null

    suspend fun fetch(baseUrl: String, slug: String): KumaState = withContext(Dispatchers.IO) {
        try {
            val base = baseUrl.trimEnd('/')
            val monitorsBody = NetworkClient.client.newCall(NetworkClient.get("$base/api/status-page/$slug")).execute().use { resp ->
                if (!resp.isSuccessful) throw java.io.IOException("HTTP ${resp.code}")
                resp.body?.string() ?: throw java.io.IOException("empty response")
            }
            val heartbeatBody = NetworkClient.client.newCall(NetworkClient.get("$base/api/status-page/heartbeat/$slug")).execute().use { resp ->
                if (!resp.isSuccessful) throw java.io.IOException("HTTP ${resp.code}")
                resp.body?.string() ?: throw java.io.IOException("empty response")
            }
            val monitors = json.decodeFromString(StatusPageResponse.serializer(), monitorsBody)
                .publicGroupList.flatMap { it.monitorList }
            val heartbeats = json.decodeFromString(HeartbeatResponse.serializer(), heartbeatBody).heartbeatList

            var up = 0
            var down = 0
            val downNames = mutableListOf<String>()
            val details = mutableListOf<KumaMonitor>()
            for (m in monitors) {
                val beats = heartbeats[m.id.toString()].orEmpty()
                val latest = beats.lastOrNull() ?: continue
                when (latest.status) {
                    1 -> up++
                    0 -> { down++; downNames += m.name }
                    else -> {} // pending(2)/maintenance(3) counted as neither up nor down
                }
                // Walk back to the first beat of the current run of this status.
                val runStart = beats.indexOfLast { it.status != latest.status } + 1
                details += KumaMonitor(
                    name = m.name,
                    status = latest.status,
                    since = parseBeatTime(beats[runStart].time),
                    pingMs = latest.ping,
                    sinceIsLowerBound = runStart == 0,
                )
            }
            val snapshot = KumaSnapshot(up, down, downNames, details)
            last = snapshot
            KumaState.Available(snapshot, stale = false)
        } catch (e: Exception) {
            Log.w("Hearth/Kuma", "fetch failed: $e")
            val cached = last
            if (cached != null) KumaState.Available(cached, stale = true) else KumaState.Unavailable
        }
    }
}

/** Kuma reports beat times in UTC as "yyyy-MM-dd HH:mm:ss" with 0-9 fractional digits. */
private val BEAT_TIME = DateTimeFormatterBuilder()
    .appendPattern("yyyy-MM-dd HH:mm:ss")
    .optionalStart().appendFraction(ChronoField.NANO_OF_SECOND, 0, 9, true).optionalEnd()
    .toFormatter()

private fun parseBeatTime(value: String): Instant? =
    runCatching { LocalDateTime.parse(value, BEAT_TIME).toInstant(ZoneOffset.UTC) }.getOrNull()
