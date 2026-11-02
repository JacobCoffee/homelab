package dev.jacobcoffee.hearth.ui.theme

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import dev.jacobcoffee.hearth.theme.ParticleType
import dev.jacobcoffee.hearth.theme.ResolvedTheme
import dev.jacobcoffee.hearth.theme.ThemeAssets
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.max
import kotlin.math.sin

data class HearthColors(
    val background: Color,
    val backgroundGradient: List<Color>?,
    val surface: Color,
    val surfaceFocused: Color,
    val onSurface: Color,
    val onSurfaceMuted: Color,
    val accent: Color,
    val focusRing: Color,
    /** Light pool under the focused tile; null when the theme has none. */
    val focusLight: Color?,
) {
    /** Resting edge of tiles and dividers: the muted text hue, faint. */
    val line: Color get() = onSurfaceMuted.copy(alpha = 0.26f)
    /** Ornament line work at rest (rules, separators, inner frame). */
    val ornamentDim: Color get() = accent.copy(alpha = 0.62f)
    /** Problems (a monitor down, protection off): text or a line, never a fill. */
    val warning: Color get() = Color(0xFFE0A479)
}

data class HearthTypography(
    val display: FontFamily,
    val body: FontFamily,
    val displayCaps: Boolean,
)

val LocalHearthColors = staticCompositionLocalOf<HearthColors> { error("No HearthTheme applied") }
val LocalHearthTypography = compositionLocalOf<HearthTypography> { error("No HearthTheme applied") }
val LocalHearthTileRadius = compositionLocalOf { 8.dp }
val LocalHearthTheme = staticCompositionLocalOf<ResolvedTheme> { error("No HearthTheme applied") }

private fun parseColor(hex: String): Color = Color(android.graphics.Color.parseColor(hex))

/** Seconds per full breath of the wallpaper drift: slow enough to notice only over minutes. */
private const val DRIFT_PERIOD_S = 120.0
private const val PAN_PERIOD_S = 180.0

/**
 * Applies [resolved]'s colors, fonts, wallpaper and tile shape to [content]. Fonts and the
 * wallpaper bitmap are loaded off the main thread and cached by theme id+version. Owns the
 * ambient clock, which runs only when the theme has motion and [reduceMotion] is off.
 */
@Composable
fun HearthTheme(resolved: ResolvedTheme, reduceMotion: Boolean, content: @Composable () -> Unit) {
    val context = LocalContext.current
    val json = resolved.json

    val colors = remember(resolved) {
        HearthColors(
            background = parseColor(json.colors.background),
            backgroundGradient = json.colors.backgroundGradient?.map { parseColor(it) },
            surface = parseColor(json.colors.surface),
            surfaceFocused = parseColor(json.colors.surfaceFocused),
            onSurface = parseColor(json.colors.onSurface),
            onSurfaceMuted = parseColor(json.colors.onSurfaceMuted),
            accent = parseColor(json.colors.accent),
            focusRing = parseColor(json.colors.focusRing),
            focusLight = json.focusLight?.let { parseColor(it) },
        )
    }

    val typography by produceState(
        initialValue = HearthTypography(FontFamily.Default, FontFamily.Default, json.displayCaps),
        key1 = resolved,
    ) {
        value = withContext(Dispatchers.IO) {
            val display = json.fonts?.display?.let { ThemeAssets.resolve(context, resolved, it) }
                ?.let { FontFamily(Font(it)) } ?: FontFamily.Default
            val body = json.fonts?.body?.let { ThemeAssets.resolve(context, resolved, it) }
                ?.let { FontFamily(Font(it)) } ?: FontFamily.Default
            HearthTypography(display, body, json.displayCaps)
        }
    }

    val screenWidthPx = with(LocalDensity.current) { LocalConfiguration.current.screenWidthDp.dp.roundToPx() }
    val screenHeightPx = with(LocalDensity.current) { LocalConfiguration.current.screenHeightDp.dp.roundToPx() }
    val wallpaper by produceState<Bitmap?>(initialValue = null, key1 = resolved) {
        value = json.wallpaper?.let { ThemeAssets.resolve(context, resolved, it) }?.let { file ->
            withContext(Dispatchers.IO) { ThemeAssets.decodeWallpaper(file, screenWidthPx, screenHeightPx) }
        }
    }

    val drifts = json.wallpaper != null && json.wallpaperMotion == "drift"
    val hasParticles = (json.effect?.particleType() ?: ParticleType.none) != ParticleType.none
    val clock = rememberAmbientClock(enabled = !reduceMotion && (drifts || hasParticles))

    val tileRadius = json.tile?.cornerRadius?.dp ?: 8.dp

    CompositionLocalProvider(
        LocalHearthColors provides colors,
        LocalHearthTypography provides typography,
        LocalHearthTileRadius provides tileRadius,
        LocalHearthTheme provides resolved,
        LocalAmbientClock provides clock,
    ) {
        Box(modifier = Modifier.fillMaxSize().background(colors.background)) {
            colors.backgroundGradient?.let { stops ->
                Box(modifier = Modifier.fillMaxSize().background(Brush.verticalGradient(stops)))
            }
            wallpaper?.let { bmp ->
                val driftClock = if (drifts) clock else null
                val image = remember(bmp) { bmp.asImageBitmap() }
                // Drawn, not moved with a graphicsLayer: a layer whose transform changes counts as
                // a layout change, and with Hearth's own accessibility service enabled every one
                // makes Compose re-check its whole accessibility tree. Reading the clock here only
                // redraws.
                Canvas(Modifier.fillMaxSize()) {
                    // A slow push-in that never drops below 1.01x, so no edge ever shows.
                    val t = (driftClock?.value ?: 0L) / 1_000_000_000.0
                    val breath = if (driftClock == null) 0f else sin(2 * Math.PI * t / DRIFT_PERIOD_S).toFloat()
                    val pan = if (driftClock == null) 0f else sin(2 * Math.PI * t / PAN_PERIOD_S).toFloat()
                    // Cover the screen like ContentScale.Crop, then push in around the center.
                    val fit = max(size.width / image.width, size.height / image.height) * (1.04f + 0.03f * breath)
                    val left = (size.width - image.width * fit) / 2 + size.width * 0.008f * pan
                    val top = (size.height - image.height * fit) / 2
                    withTransform({
                        translate(left, top)
                        scale(fit, fit, pivot = Offset.Zero)
                    }) { drawImage(image) }
                }
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color.Black.copy(alpha = (json.wallpaperDim ?: 0.3).toFloat()))
                )
                // The painting stays bright through the middle; text above and tiles below sit on
                // the theme's own night instead of a flat black box.
                Box(
                    modifier = Modifier.fillMaxSize().background(
                        Brush.verticalGradient(
                            0f to colors.background.copy(alpha = 0.78f),
                            0.26f to colors.background.copy(alpha = 0.10f),
                            0.48f to Color.Transparent,
                            0.70f to colors.background.copy(alpha = 0.42f),
                            1f to colors.background.copy(alpha = 0.88f),
                        )
                    )
                )
            }
            content()
        }
    }
}
