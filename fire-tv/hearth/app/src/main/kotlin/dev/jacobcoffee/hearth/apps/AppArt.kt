package dev.jacobcoffee.hearth.apps

import android.content.Context
import android.graphics.Bitmap
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.Drawable
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.core.graphics.drawable.toBitmap
import java.util.concurrent.ConcurrentHashMap

/**
 * Banners and icons, read from each app's APK once and kept until the app is updated or
 * removed. Reading them means opening the other app's resources, so it is too slow for every
 * return to Home; [AppRepository.refresh] warms the banners off the main thread.
 *
 * Banners are kept at the width the grid draws them, not their own (640 px wide at this
 * screen's density, against tiles about 260 px wide): a sixth of the memory, here and on the GPU.
 * Thread-safe: loads may race, and the loser's bitmap is simply dropped.
 */
object AppArt {
    /** [image] is null for a banner with no artwork; the tile then shows icon and label. */
    private class Banner(val widthPx: Int, val image: ImageBitmap?)

    private val banners = ConcurrentHashMap<String, Banner>()
    private val icons = ConcurrentHashMap<String, ImageBitmap>()

    /** The width the grid last drew banners at; [warmBanners] reads them at that size. */
    @Volatile private var bannerWidthPx = 0

    fun banner(context: Context, app: AppInfo, widthPx: Int): ImageBitmap? {
        if (!app.tv) return null
        bannerWidthPx = widthPx
        banners[app.artKey]?.takeIf { it.widthPx == widthPx }?.let { return it.image }
        val pm = context.packageManager
        val image = runCatching { pm.getActivityBanner(app.component) ?: pm.getApplicationBanner(app.packageName) }
            .getOrNull()
            // A sizeless banner (Fire OS 8.1's Troubleshooting app ships a bare gradient <shape>)
            // has no artwork.
            ?.takeIf { it.intrinsicWidth > 0 && it.intrinsicHeight > 0 }
            ?.scaledTo(widthPx)
            ?.ready()
        banners[app.artKey] = Banner(widthPx, image)
        return image
    }

    /** Reads [apps]' banners ahead of the grid, once it has drawn them at least once. */
    fun warmBanners(context: Context, apps: List<AppInfo>) {
        val widthPx = bannerWidthPx
        if (widthPx > 0) apps.forEach { banner(context, it, widthPx) }
    }

    fun icon(context: Context, app: AppInfo): ImageBitmap = icons.getOrPut(app.artKey) {
        val pm = context.packageManager
        (runCatching { pm.getActivityIcon(app.component) }.getOrNull() ?: pm.defaultActivityIcon).toImageBitmap()
    }

    /** Drops art for apps that were updated or removed since it was read. */
    fun retain(apps: Collection<AppInfo>) {
        val keep = apps.mapTo(HashSet()) { it.artKey }
        banners.keys.retainAll(keep)
        icons.keys.retainAll(keep)
    }

    private val AppInfo.artKey: String get() = component.flattenToString() + "|" + apkPath

    /**
     * At most [maxW] wide. Bitmaps halve with filtering until the last step is under 2x, as one
     * bilinear step that large skips source pixels and shimmers; anything else is drawn at size.
     */
    private fun Drawable.scaledTo(maxW: Int): Bitmap {
        if (this is BitmapDrawable && bitmap != null) {
            var b: Bitmap = bitmap
            while (b.width / 2 >= maxW) b = Bitmap.createScaledBitmap(b, b.width / 2, b.height / 2, true)
            return if (b.width <= maxW) b else Bitmap.createScaledBitmap(b, maxW, b.height * maxW / b.width, true)
        }
        val w = minOf(intrinsicWidth, maxW)
        return toBitmap(w, intrinsicHeight * w / intrinsicWidth)
    }

    /** Starts the GPU upload ahead of the first frame that draws it. */
    private fun Bitmap.ready(): ImageBitmap {
        prepareToDraw()
        return asImageBitmap()
    }
}

/**
 * [toBitmap] throws for drawables with no intrinsic size (shapes, colors); those get
 * [fallbackPx] square. An exception here would crash Hearth and the Home redirect with it.
 */
fun Drawable.toImageBitmap(fallbackPx: Int = 192): ImageBitmap {
    val bitmap = toBitmap(
        intrinsicWidth.takeIf { it > 0 } ?: fallbackPx,
        intrinsicHeight.takeIf { it > 0 } ?: fallbackPx,
    )
    bitmap.prepareToDraw()
    return bitmap.asImageBitmap()
}
