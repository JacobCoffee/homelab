package dev.jacobcoffee.hearth.homekey

import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import dev.jacobcoffee.hearth.MainActivity
import dev.jacobcoffee.hearth.apps.FireTvSettings
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket

/**
 * The Fire TV half of the TCL-remote bridge (tcl-tv/bridge): the TV's key watcher connects,
 * sends one word, and this wakes the stick and opens that app. Hosted by the accessibility
 * service, so it starts at boot with it and its activity starts are not blocked as background
 * starts. Only the words in [APPS] do anything, and only LAN clients are served.
 */
class RemoteBridge(private val context: Context) {

    companion object {
        const val PORT = 47800
        private const val TAG = "Hearth/Bridge"
        private const val READ_TIMEOUT_MS = 2000
        private const val MAX_WORD = 32
        private const val WAKE_MS = 3000L
        private const val SETTINGS = "settings"

        /** Word -> package. "hearth" opens Hearth itself; "settings" opens Fire TV settings. */
        val APPS = mapOf(
            "jellyfin" to "org.jellyfin.androidtv",
            "netflix" to "com.netflix.ninja",
            "prime" to "com.amazon.firebat",
            "youtube" to "com.amazon.firetv.youtube",
            "hbo" to "com.hbo.hbonow",
            "disney" to "com.disney.disneyplus",
            "hearth" to "dev.jacobcoffee.hearth",
            "settings" to SETTINGS,
        )
    }

    private val main = Handler(Looper.getMainLooper())
    @Volatile private var server: ServerSocket? = null

    fun start() {
        if (server != null) return
        val socket = try {
            ServerSocket().apply {
                reuseAddress = true
                bind(InetSocketAddress(PORT))
            }
        } catch (e: IOException) {
            Log.w(TAG, "cannot listen on $PORT (another listener running?)", e)
            return
        }
        server = socket
        Thread({ acceptLoop(socket) }, "hearth-bridge").apply { isDaemon = true }.start()
        Log.i(TAG, "listening on $PORT")
    }

    fun stop() {
        server?.let { runCatching { it.close() } }
        server = null
    }

    private fun acceptLoop(socket: ServerSocket) {
        while (!socket.isClosed) {
            val client = try { socket.accept() } catch (e: IOException) { break }
            client.use { handle(it) }
        }
    }

    private fun handle(client: Socket) {
        val from = client.inetAddress
        if (!isLan(from)) {
            Log.w(TAG, "refused ${from.hostAddress}: not a LAN address")
            return
        }
        client.soTimeout = READ_TIMEOUT_MS
        val word = try {
            val bytes = ByteArray(MAX_WORD)
            var n = 0
            val input = client.getInputStream()
            while (n < MAX_WORD) {
                val b = input.read()
                if (b < 0 || b == '\n'.code) break
                bytes[n++] = b.toByte()
            }
            String(bytes, 0, n, Charsets.US_ASCII).trim().lowercase()
        } catch (e: IOException) {
            return
        }
        val pkg = APPS[word] ?: return
        Log.i(TAG, "$word from ${from.hostAddress}")
        main.post { wakeAndOpen(pkg) }
    }

    private fun isLan(address: InetAddress): Boolean =
        address.isSiteLocalAddress || address.isLoopbackAddress || address.isLinkLocalAddress

    @Suppress("DEPRECATION") // ACQUIRE_CAUSES_WAKEUP on a screen lock is the only app-level way to wake a TV.
    private fun wakeAndOpen(pkg: String) {
        val power = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        if (!power.isInteractive) {
            power.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP or PowerManager.ON_AFTER_RELEASE,
                "hearth:bridge",
            ).acquire(WAKE_MS)
        }
        if (pkg == context.packageName) {
            MainActivity.open(context)
            return
        }
        if (pkg == SETTINGS) {
            FireTvSettings.open(context)
            return
        }
        val pm = context.packageManager
        val intent = pm.getLeanbackLaunchIntentForPackage(pkg) ?: pm.getLaunchIntentForPackage(pkg)
        if (intent == null) {
            Log.w(TAG, "$pkg is not installed or has no launcher activity")
            return
        }
        context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED))
    }
}
