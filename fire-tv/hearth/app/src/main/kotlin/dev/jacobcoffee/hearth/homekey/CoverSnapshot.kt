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
 * instead of a placeholder. Captured with PixelCopy (a GPU copy, scaled into a half-size
 * bitmap, off the main thread) once the home screen has settled, kept in memory and on disk
 * for after a restart. JPEG, not WebP: it encodes many times faster on this CPU, and the cover
 * is only on screen for a moment.
 */
object CoverSnapshot {
    private const val TAG = "Hearth/Cover"
    private const val FILE = "cover.jpg"

    private val worker by lazy { HandlerThread("hearth-cover").apply { start() } }
    private val workerHandler by lazy { Handler(worker.looper) }

    @Volatile private var latest: Bitmap? = null

    fun capture(activity: Activity) {
        val decor = activity.window?.decorView ?: return
        if (decor.width <= 0 || decor.height <= 0) return
        val half = Bitmap.createBitmap(decor.width / 2, decor.height / 2, Bitmap.Config.ARGB_8888)
        val appContext = activity.applicationContext
        try {
            PixelCopy.request(activity.window, half, { result ->
                if (result != PixelCopy.SUCCESS) {
                    half.recycle()
                    return@request
                }
                latest = half
                runCatching {
                    File(appContext.filesDir, FILE).outputStream().use { half.compress(Bitmap.CompressFormat.JPEG, 88, it) }
                }.onFailure { Log.w(TAG, "could not save the cover: $it") }
            }, workerHandler)
        } catch (e: IllegalArgumentException) {
            half.recycle() // The window has no surface yet.
        }
    }

    /** The last captured home screen, or null before the first capture. */
    fun load(context: Context): Bitmap? = latest ?: runCatching {
        File(context.filesDir, FILE).takeIf { it.isFile }?.let { BitmapFactory.decodeFile(it.path) }
    }.getOrNull()?.also { latest = it }
}
