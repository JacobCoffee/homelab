package dev.jacobcoffee.hearth.widgets

import android.util.Log
import dev.jacobcoffee.hearth.net.NetworkClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime

enum class EventKind { raid, dungeon, leveling, pvp, social, transmog, other }

data class GuildEvent(
    val kind: EventKind,
    val title: String,
    val location: String?,
    val startsAt: ZonedDateTime,
)

sealed class GuildEventsState {
    object Loading : GuildEventsState()
    data class Available(val events: List<GuildEvent>, val stale: Boolean) : GuildEventsState()
    object Unavailable : GuildEventsState()
}

@Serializable
private data class GuildEventJson(
    val kind: String = "other",
    val title: String = "",
    val location: String? = null,
    @SerialName("starts_at") val startsAt: String,
    @SerialName("ends_at") val endsAt: String? = null,
    val status: String = "scheduled",
    val size: Int? = null,
    val ep: Int? = null,
    @SerialName("ep_override") val epOverride: Int? = null,
    val notes: String? = null,
    val counts: JsonElement? = null,
    val series: JsonElement? = null,
)

class GuildEventsRepository {
    private val json = Json { ignoreUnknownKeys = true }
    @Volatile private var last: List<GuildEvent>? = null

    suspend fun fetch(baseUrl: String, maxEvents: Int): GuildEventsState = withContext(Dispatchers.IO) {
        try {
            val url = "${baseUrl.trimEnd('/')}/api/events?when=upcoming"
            val body = NetworkClient.client.newCall(NetworkClient.get(url)).execute().use { resp ->
                if (!resp.isSuccessful) throw java.io.IOException("HTTP ${resp.code}")
                resp.body?.string() ?: throw java.io.IOException("empty response")
            }
            val raw = json.decodeFromString(kotlinx.serialization.builtins.ListSerializer(GuildEventJson.serializer()), body)
            val localZone = ZoneId.systemDefault()
            val events = raw
                .filter { it.status == "scheduled" }
                .mapNotNull { e ->
                    val kind = runCatching { EventKind.valueOf(e.kind) }.getOrDefault(EventKind.other)
                    val starts = runCatching { ZonedDateTime.ofInstant(Instant.parse(e.startsAt), localZone) }.getOrNull()
                        ?: return@mapNotNull null
                    GuildEvent(kind, e.title, e.location, starts)
                }
                .sortedBy { it.startsAt }
                .take(maxEvents)
            last = events
            GuildEventsState.Available(events, stale = false)
        } catch (e: Exception) {
            Log.w("Hearth/Guild", "fetch failed: $e")
            val cached = last
            if (cached != null) GuildEventsState.Available(cached, stale = true) else GuildEventsState.Unavailable
        }
    }
}
