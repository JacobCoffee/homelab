package dev.jacobcoffee.hearth.widgets

import android.content.Context
import android.util.Log
import dev.jacobcoffee.hearth.config.TvConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.InetSocketAddress
import java.net.Socket

/**
 * Talks to the TV's listener (tcl-tv/bridge/tcl-listen.sh): one word per connection, the same
 * protocol the TV uses to reach Hearth. Words: tvsettings, tvhome, hdmi1..hdmi4, off,
 * sleep:<minutes>, sleep:cancel.
 */
object TvLink {
    private const val TIMEOUT_MS = 2000
    private const val PREFS = "hearth_prefs"
    private const val KEY_SLEEP_UNTIL = "tv_sleep_until"

    /** Sends [word]; false if the TV's listener could not be reached. */
    suspend fun send(config: TvConfig, word: String): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            Socket().use { socket ->
                socket.connect(InetSocketAddress(config.host, config.port), TIMEOUT_MS)
                socket.getOutputStream().apply { write("$word\n".toByteArray(Charsets.US_ASCII)); flush() }
            }
        }.onFailure { Log.w("Hearth/Tv", "send $word failed: $it") }.isSuccess
    }

    /** Wall-clock millis when the TV's sleep timer fires, or null when none is set from Hearth. */
    fun sleepUntil(context: Context): Long? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getLong(KEY_SLEEP_UNTIL, 0L)
            .takeIf { it > System.currentTimeMillis() }

    fun rememberSleep(context: Context, untilMillis: Long?) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putLong(KEY_SLEEP_UNTIL, untilMillis ?: 0L).apply()
    }
}
