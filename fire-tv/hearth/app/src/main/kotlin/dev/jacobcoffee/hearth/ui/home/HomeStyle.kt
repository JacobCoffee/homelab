package dev.jacobcoffee.hearth.ui.home

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Paint
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import dev.jacobcoffee.hearth.ui.theme.HearthColors
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors
import dev.jacobcoffee.hearth.ui.theme.LocalHearthTypography
import kotlinx.coroutines.delay

/**
 * The home screen's text roles. Display roles take the theme's display face and, on caps
 * themes, wide tracking; labels are always small tracked caps in the body face. Everything
 * that sits over a painting carries a soft night glow, never a hard drop shadow.
 */
class HomeType(colors: HearthColors, displayFont: FontFamily, bodyFont: FontFamily, private val caps: Boolean) {
    private val shade = Shadow(color = colors.background.copy(alpha = 0.92f), offset = Offset(0f, 2f), blurRadius = 14f)
    private fun tracked(em: Float): TextUnit = if (caps) em.em else 0.01.em

    val greeting = TextStyle(fontFamily = displayFont, fontSize = 26.sp, letterSpacing = tracked(0.24f), color = colors.onSurface, shadow = shade)
    val clock = TextStyle(fontFamily = displayFont, fontSize = 46.sp, lineHeight = 46.sp, letterSpacing = 0.02.em, color = colors.onSurface, shadow = shade)
    val questTitle = TextStyle(fontFamily = displayFont, fontSize = 15.sp, letterSpacing = tracked(0.14f), color = colors.onSurface, shadow = shade)
    val questWhen = TextStyle(fontFamily = displayFont, fontSize = 15.sp, letterSpacing = tracked(0.08f), color = colors.onSurface, shadow = shade)
    val body = TextStyle(fontFamily = bodyFont, fontSize = 16.sp, color = colors.onSurface, shadow = shade)
    val bodyMuted = body.copy(color = colors.onSurfaceMuted)
    val aside = body.copy(fontSize = 15.sp, fontStyle = FontStyle.Italic, color = colors.onSurfaceMuted.copy(alpha = 0.8f))
    val label = TextStyle(fontFamily = bodyFont, fontSize = 11.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.18.em, color = colors.onSurfaceMuted, shadow = shade)
}

/**
 * The game's quest-giver mark, drawn: a tapered gold bar over a dot, with a soft glow behind
 * it. The one glow on the home screen.
 */
@Composable
fun QuestMark(modifier: Modifier = Modifier) {
    val colors = LocalHearthColors.current
    val gold = colors.focusLight ?: colors.accent
    Canvas(modifier.size(width = 22.dp, height = 36.dp)) {
        val cx = size.width / 2
        drawCircle(
            Brush.radialGradient(listOf(gold.copy(alpha = 0.42f), Color.Transparent), center = Offset(cx, size.height * 0.5f), radius = size.height * 0.62f),
            radius = size.height * 0.62f,
            center = Offset(cx, size.height * 0.5f),
        )
        val top = size.height * 0.14f
        val barBottom = size.height * 0.64f
        val wTop = 3.2.dp.toPx()
        val wBottom = 1.5.dp.toPx()
        val bar = Path().apply {
            moveTo(cx - wTop, top)
            quadraticBezierTo(cx, top - wTop * 0.9f, cx + wTop, top)
            lineTo(cx + wBottom, barBottom)
            quadraticBezierTo(cx, barBottom + wBottom, cx - wBottom, barBottom)
            close()
        }
        drawPath(bar, gold)
        drawCircle(gold, radius = 2.6.dp.toPx(), center = Offset(cx, size.height * 0.80f))
    }
}

@Composable
fun rememberHomeType(): HomeType {
    val colors = LocalHearthColors.current
    val typography = LocalHearthTypography.current
    return remember(colors, typography) { HomeType(colors, typography.display, typography.body, typography.displayCaps) }
}

/**
 * A soft band of the theme's night behind copy laid over bright art: darkest behind the text,
 * fading out on every side past the element's bounds, so it reads as shade, never as a box.
 */
@Composable
fun Modifier.nightScrim(spreadX: Dp = 48.dp, spreadY: Dp = 20.dp, strength: Float = 0.66f): Modifier {
    val night = LocalHearthColors.current.background
    return drawBehind {
        val sx = spreadX.toPx()
        val sy = spreadY.toPx()
        val topLeft = Offset(-sx, -sy)
        val area = androidx.compose.ui.geometry.Size(size.width + sx * 2, size.height + sy * 2)
        drawIntoCanvas { canvas ->
            canvas.saveLayer(androidx.compose.ui.geometry.Rect(topLeft, area), Paint())
            drawRect(
                Brush.verticalGradient(
                    0f to Color.Transparent,
                    0.32f to night.copy(alpha = strength),
                    0.68f to night.copy(alpha = strength),
                    1f to Color.Transparent,
                    startY = -sy,
                    endY = size.height + sy,
                ),
                topLeft,
                area,
            )
            // Fade the band's ends with a horizontal mask so its sides dissolve too.
            drawRect(
                Brush.horizontalGradient(
                    0f to Color.Transparent,
                    0.1f to Color.Black,
                    0.9f to Color.Black,
                    1f to Color.Transparent,
                    startX = -sx,
                    endX = size.width + sx,
                ),
                topLeft,
                area,
                blendMode = BlendMode.DstIn,
            )
            canvas.restore()
        }
    }
}

/** Uppercases on caps themes; labels call [String.uppercase] themselves. */
@Composable
fun displayText(text: String): String =
    if (LocalHearthTypography.current.displayCaps) text.uppercase() else text

/**
 * A 1px rule fading in from transparent and ending in an outlined diamond. [flip] puts the
 * diamond at the start, so two rules can flank something symmetrically.
 */
@Composable
fun HairlineRule(modifier: Modifier = Modifier, color: Color = LocalHearthColors.current.ornamentDim, flip: Boolean = false) {
    Canvas(modifier.height(9.dp)) {
        val d = 7.dp.toPx()
        val gap = d + 2.dp.toPx()
        val y = size.height / 2
        val stroke = 1.dp.toPx()
        if (flip) {
            drawLine(Brush.horizontalGradient(listOf(color, Color.Transparent), gap, size.width), Offset(gap, y), Offset(size.width, y), stroke)
            diamond(center = Offset(d / 2, y), side = d * 0.7f, color = color, filled = false)
        } else {
            val end = size.width - gap
            drawLine(Brush.horizontalGradient(listOf(Color.Transparent, color), 0f, end), Offset(0f, y), Offset(end, y), stroke)
            diamond(center = Offset(size.width - d / 2, y), side = d * 0.7f, color = color, filled = false)
        }
    }
}

/**
 * The guild's star rule: a hairline fading in from both ends, stopping short of a solid
 * four-point star at center.
 */
@Composable
fun StarRule(modifier: Modifier = Modifier, color: Color = LocalHearthColors.current.ornamentDim) {
    Canvas(modifier.height(12.dp)) {
        val cx = size.width / 2
        val y = size.height / 2
        val gap = 14.dp.toPx()
        val stroke = 1.dp.toPx()
        drawLine(Brush.horizontalGradient(listOf(Color.Transparent, color), 0f, cx - gap), Offset(0f, y), Offset(cx - gap, y), stroke)
        drawLine(Brush.horizontalGradient(listOf(color, Color.Transparent), cx + gap, size.width), Offset(cx + gap, y), Offset(size.width, y), stroke)
        val r = 6.dp.toPx()
        val k = r * 0.22f
        val star = Path().apply {
            moveTo(cx, y - r)
            quadraticBezierTo(cx + k, y - k, cx + r, y)
            quadraticBezierTo(cx + k, y + k, cx, y + r)
            quadraticBezierTo(cx - k, y + k, cx - r, y)
            quadraticBezierTo(cx - k, y - k, cx, y - r)
            close()
        }
        drawPath(star, color.copy(alpha = 1f))
    }
}

/** A small rotated square: a separator between links and a status mark. */
@Composable
fun Diamond(color: Color, size: Dp = 5.dp, filled: Boolean = true, modifier: Modifier = Modifier) {
    Canvas(modifier.size(size + 2.dp)) {
        diamond(Offset(this.size.width / 2, this.size.height / 2), size.toPx() * 0.72f, color, filled)
    }
}

private fun androidx.compose.ui.graphics.drawscope.DrawScope.diamond(center: Offset, side: Float, color: Color, filled: Boolean) {
    rotate(45f, pivot = center) {
        val tl = Offset(center.x - side / 2, center.y - side / 2)
        val sz = androidx.compose.ui.geometry.Size(side, side)
        if (filled) drawRect(color, tl, sz) else drawRect(color, tl, sz, style = Stroke(width = 1.dp.toPx()))
    }
}

/**
 * Re-runs [fetch] every [periodMs] while the screen is resumed and keeps the last result.
 * A result that [failed] is retried after [retryMs] instead of a whole period.
 * Nothing polls behind an app the user opened.
 */
@Composable
fun <T> rememberPolled(
    key: Any?,
    initial: T,
    periodMs: Long,
    retryMs: Long = periodMs,
    failed: (T) -> Boolean = { false },
    fetch: suspend () -> T,
): State<T> {
    val state = remember(key) { mutableStateOf(initial) }
    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(lifecycleOwner, key) {
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            while (true) {
                val result = fetch()
                state.value = result
                delay(if (failed(result)) retryMs else periodMs)
            }
        }
    }
    return state
}
