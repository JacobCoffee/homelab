package dev.jacobcoffee.hearth.media

import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.os.Handler
import android.os.Looper
import dev.jacobcoffee.hearth.net.NetworkClient
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.distinctUntilChanged

/** What another app is playing, as one snapshot of its media session. */
data class NowPlaying(
    val packageName: String,
    val appLabel: String,
    val title: String,
    val artist: String?,
    /** Album art from the session, already scaled to the strip; null when it only gave a URI. */
    val art: Bitmap?,
    val artUri: String?,
    val playing: Boolean,
    /** 0 when the session doesn't say (live radio, some video apps). */
    val durationMs: Long,
    val positionMs: Long,
    /** SystemClock.elapsedRealtime() at which [positionMs] was true. */
    val positionUpdatedAt: Long,
    val speed: Float,
    val canPrevious: Boolean,
    val canNext: Boolean,
    val controls: MediaController.TransportControls,
    /** The player's own screen for this session (Spotify's now-playing view). */
    val sessionActivity: PendingIntent?,
) {
    fun positionAt(nowElapsed: Long): Long = extrapolatePosition(positionMs, positionUpdatedAt, speed, playing, durationMs, nowElapsed)
}

/**
 * Where playback is at [nowElapsed]: sessions report a position only when something changes,
 * so a playing track advances from its last report at its speed. Never before 0 or past the
 * end of a track of known length.
 */
fun extrapolatePosition(positionMs: Long, updatedAt: Long, speed: Float, playing: Boolean, durationMs: Long, nowElapsed: Long): Long {
    val p = if (playing) positionMs + ((nowElapsed - updatedAt) * speed).toLong() else positionMs
    val floor = p.coerceAtLeast(0L)
    return if (durationMs > 0) floor.coerceAtMost(durationMs) else floor
}

/** "3:05", or "1:02:03" past an hour. */
fun formatTrackTime(ms: Long): String {
    val s = ms.coerceAtLeast(0L) / 1000
    val h = s / 3600
    val m = (s % 3600) / 60
    val sec = s % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, sec) else "%d:%02d".format(m, sec)
}

/**
 * Follows the media session worth showing: the first playing one, else the first paused one
 * with a title, so a paused song can be resumed from Home. Listens only while collected;
 * Home collects it while resumed, so nothing runs behind an app.
 */
class NowPlayingSource(context: Context) {
    private val app = context.applicationContext
    private val manager = app.getSystemService(MediaSessionManager::class.java)
    private val listener = ComponentName(app, MediaListener::class.java)
    private val artPx = (ART_DP * app.resources.displayMetrics.density).toInt()
    private val labels = HashMap<String, String>()

    // The last scaled cover, reused while the session keeps the same metadata object.
    private var artFor: MediaMetadata? = null
    private var artScaled: Bitmap? = null

    fun updates(): Flow<NowPlaying?> = callbackFlow {
        val handler = Handler(Looper.getMainLooper())
        var current: MediaController? = null

        lateinit var refresh: () -> Unit
        val callback = object : MediaController.Callback() {
            override fun onPlaybackStateChanged(state: PlaybackState?) = refresh()
            override fun onMetadataChanged(metadata: MediaMetadata?) = refresh()
            override fun onSessionDestroyed() = refresh()
        }
        // Re-choosing on every change also catches another app starting while this one pauses.
        refresh = {
            val chosen = runCatching { choose(manager.getActiveSessions(listener)) }.getOrNull()
            if (chosen?.sessionToken != current?.sessionToken) {
                current?.unregisterCallback(callback)
                chosen?.registerCallback(callback, handler)
                current = chosen
            }
            trySend(chosen?.let(::snapshot))
        }
        val sessions = MediaSessionManager.OnActiveSessionsChangedListener { refresh() }
        try {
            manager.addOnActiveSessionsChangedListener(sessions, listener, handler)
            refresh()
        } catch (e: SecurityException) {
            // MediaListener isn't an enabled notification listener yet (see tools/install.sh).
            trySend(null)
        }
        awaitClose {
            runCatching { manager.removeOnActiveSessionsChangedListener(sessions) }
            current?.unregisterCallback(callback)
        }
    }.distinctUntilChanged()

    private fun choose(controllers: List<MediaController>): MediaController? {
        val candidates = controllers.filter { it.packageName != app.packageName && it.metadata?.title() != null }
        return candidates.firstOrNull { it.playbackState?.state in PLAYING_STATES }
            ?: candidates.firstOrNull { it.playbackState?.state == PlaybackState.STATE_PAUSED }
    }

    private fun snapshot(c: MediaController): NowPlaying? {
        val meta = c.metadata ?: return null
        val title = meta.title() ?: return null
        val state = c.playbackState
        val actions = state?.actions ?: 0L
        return NowPlaying(
            packageName = c.packageName,
            appLabel = labelFor(c.packageName),
            title = title,
            artist = listOf(MediaMetadata.METADATA_KEY_ARTIST, MediaMetadata.METADATA_KEY_ALBUM_ARTIST, MediaMetadata.METADATA_KEY_DISPLAY_SUBTITLE)
                .firstNotNullOfOrNull { meta.getString(it)?.takeIf(String::isNotBlank) },
            art = scaledArt(meta),
            artUri = listOf(MediaMetadata.METADATA_KEY_ALBUM_ART_URI, MediaMetadata.METADATA_KEY_ART_URI, MediaMetadata.METADATA_KEY_DISPLAY_ICON_URI)
                .firstNotNullOfOrNull { meta.getString(it)?.takeIf(String::isNotBlank) },
            playing = state?.state in PLAYING_STATES,
            durationMs = meta.getLong(MediaMetadata.METADATA_KEY_DURATION).coerceAtLeast(0L),
            positionMs = state?.position ?: 0L,
            positionUpdatedAt = state?.lastPositionUpdateTime ?: 0L,
            speed = state?.playbackSpeed?.takeIf { it > 0f } ?: 1f,
            canPrevious = actions and PlaybackState.ACTION_SKIP_TO_PREVIOUS != 0L,
            canNext = actions and PlaybackState.ACTION_SKIP_TO_NEXT != 0L,
            controls = c.transportControls,
            sessionActivity = c.sessionActivity,
        )
    }

    private fun scaledArt(meta: MediaMetadata): Bitmap? {
        if (meta === artFor) return artScaled
        val raw = listOf(MediaMetadata.METADATA_KEY_ALBUM_ART, MediaMetadata.METADATA_KEY_ART, MediaMetadata.METADATA_KEY_DISPLAY_ICON)
            .firstNotNullOfOrNull { meta.getBitmap(it) }
        artFor = meta
        artScaled = raw?.let { fitSquare(it, artPx) }
        return artScaled
    }

    private fun labelFor(pkg: String): String = labels.getOrPut(pkg) {
        val pm = app.packageManager
        runCatching { pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString() }.getOrDefault(pkg)
    }

    /** Loads a session's art URI (http(s) or content://), scaled to the strip. Call off the main thread. */
    fun loadArt(uri: String): Bitmap? = runCatching {
        val bytes = if (uri.startsWith("http://") || uri.startsWith("https://")) {
            NetworkClient.client.newCall(NetworkClient.get(uri)).execute().use { r -> if (r.isSuccessful) r.body?.bytes() else null }
        } else {
            app.contentResolver.openInputStream(android.net.Uri.parse(uri))?.use { it.readBytes() }
        } ?: return@runCatching null
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        var sample = 1
        while (minOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= artPx) sample *= 2
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
            ?.let { fitSquare(it, artPx) }
    }.getOrNull()

    private companion object {
        /** The strip's cover size; NowPlayingStrip draws it at this many dp. */
        const val ART_DP = 46f
        val PLAYING_STATES = setOf(PlaybackState.STATE_PLAYING, PlaybackState.STATE_BUFFERING, PlaybackState.STATE_CONNECTING)
    }
}

private fun MediaMetadata.title(): String? =
    (getString(MediaMetadata.METADATA_KEY_TITLE) ?: getString(MediaMetadata.METADATA_KEY_DISPLAY_TITLE))?.takeIf(String::isNotBlank)

/** Center-crops [src] to a [px] square; covers are square already, video stills are not. */
private fun fitSquare(src: Bitmap, px: Int): Bitmap {
    val side = minOf(src.width, src.height)
    val cropped = if (src.width == src.height) src else Bitmap.createBitmap(src, (src.width - side) / 2, (src.height - side) / 2, side, side)
    return if (side <= px) cropped else Bitmap.createScaledBitmap(cropped, px, px, true)
}
