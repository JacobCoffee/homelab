package dev.jacobcoffee.hearth.homekey

import android.app.Activity
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.PixelCopy
import java.io.File

/**
 * A picture of Hearth's own home screen, shown by the redirect's cover window while
 * MainActivity comes up, so a Home or Guide press reveals the same painted screen at once
 * instead of a placeholder. Captured with PixelCopy (a GPU copy, off the main thread) once the
 * home screen has settled, kept in memory at half size and on disk for after a restart.
 */
object CoverSnapshot {
    private const val TAG = "Hearth/Cover"
    private const val FILE = "cover.webp"

    private val worker by lazy { HandlerThread("hearth-cover").apply { start() } }
    private val workerHandler by lazy { Handler(worker.looper) }

    @Volatile private var latest: Bitmap? = null

    fun capture(activity: Activity) {
        val decor = activity.window?.decorView ?: return
        if (decor.width <= 0 || decor.height <= 0) return
        val full = Bitmap.createBitmap(decor.width, decor.height, Bitmap.Config.ARGB_8888)
        val appContext = activity.applicationContext
        try {
            PixelCopy.request(activity.window, full, { result ->
                if (result != PixelCopy.SUCCESS) {
                    full.recycle()
                    return@request
                }
                val half = Bitmap.createScaledBitmap(full, full.width / 2, full.height / 2, true)
                full.recycle()
                latest = half
                runCatching {
                    File(appContext.filesDir, FILE).outputStream().use { half.compress(Bitmap.CompressFormat.WEBP, 85, it) }
                }.onFailure { Log.w(TAG, "could not save the cover: $it") }
            }, workerHandler)
        } catch (e: IllegalArgumentException) {
            full.recycle() // The window has no surface yet.
        }
    }

    /** The last captured home screen, or null before the first capture. */
    fun load(context: Context): Bitmap? = latest ?: runCatching {
        File(context.filesDir, FILE).takeIf { it.isFile }?.let { BitmapFactory.decodeFile(it.path) }
    }.getOrNull()?.also { latest = it }
}
