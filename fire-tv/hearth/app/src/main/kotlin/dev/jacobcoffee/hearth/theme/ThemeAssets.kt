package dev.jacobcoffee.hearth.theme

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import java.io.File

/**
 * Resolves a theme's relative file paths (wallpaper, logo, font names) to an actual File on
 * disk. Downloaded themes already live under files/themes/<id>/; bundled themes live in APK
 * assets, which Compose's font loader and BitmapFactory.decodeFile cannot read directly, so
 * bundled files are copied once into the cache and reused from there.
 */
object ThemeAssets {
    fun resolve(context: Context, theme: ResolvedTheme, relativePath: String): File? {
        return when (theme.source) {
            ThemeSource.DOWNLOADED -> {
                val f = File(context.filesDir, "themes/${theme.json.id}/$relativePath")
                if (f.isFile) f else null
            }
            ThemeSource.BUNDLED -> {
                val cached = File(context.cacheDir, "theme-assets/${theme.json.id}/${theme.json.version}/$relativePath")
                if (cached.isFile) return cached
                val assetPath = "themes/${theme.json.id}/$relativePath"
                runCatching {
                    context.assets.open(assetPath).use { input ->
                        cached.parentFile?.mkdirs()
                        cached.outputStream().use { out -> input.copyTo(out) }
                    }
                }.onFailure { return null }
                if (cached.isFile) cached else null
            }
        }
    }

    /** Decodes a wallpaper downsampled to at most [targetWidth]x[targetHeight], one at a time. */
    fun decodeWallpaper(file: File, targetWidth: Int, targetHeight: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        var sampleSize = 1
        var w = bounds.outWidth
        var h = bounds.outHeight
        while (w / 2 >= targetWidth && h / 2 >= targetHeight) {
            w /= 2; h /= 2; sampleSize *= 2
        }
        val opts = BitmapFactory.Options().apply {
            inSampleSize = sampleSize
            // Wallpapers are a dimmed background behind a scrim and UI; RGB_565 (2 bytes/px
            // instead of 4) is visually indistinguishable there and roughly halves memory.
            inPreferredConfig = Bitmap.Config.RGB_565
        }
        return BitmapFactory.decodeFile(file.path, opts)
    }
}
