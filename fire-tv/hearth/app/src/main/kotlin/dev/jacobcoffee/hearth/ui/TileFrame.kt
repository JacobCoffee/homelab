package dev.jacobcoffee.hearth.ui

import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.jacobcoffee.hearth.ui.theme.HearthColors

/** How a theme frames its app tiles: theme.json `tile.frame`. */
enum class TileFrame {
    /** One hairline edge that turns to the focus color. */
    plain,

    /**
     * The guild's chamber window: a faint outer line, a second line just inside it, and
     * bracketed corners (an L with a cut tip and a small solid diamond) that brighten and reach
     * outward on focus.
     */
    chamber;

    companion object {
        fun of(name: String?): TileFrame = entries.firstOrNull { it.name == name } ?: plain
    }
}

/** Draws [frame] over a tile's content; [lift] runs 0 (rest) to 1 (focused). */
fun DrawScope.drawTileFrame(frame: TileFrame, lift: Float, colors: HearthColors, radius: Dp) {
    val stroke = 1.dp.toPx()
    val r = CornerRadius(radius.toPx())
    // At rest the art sits back under a little night; focus brings it forward.
    drawRoundRect(colors.background.copy(alpha = 0.32f * (1f - lift)), cornerRadius = r)
    val outer = lerp(colors.line, colors.focusRing, lift)
    drawRoundRect(outer, Offset(stroke / 2, stroke / 2), Size(size.width - stroke, size.height - stroke), r, style = Stroke(stroke))

    val innerAlpha = when (frame) {
        TileFrame.plain -> lift
        TileFrame.chamber -> 0.38f + 0.62f * lift
    }
    if (innerAlpha > 0.01f) {
        val inset = 4.dp.toPx()
        drawRoundRect(
            colors.ornamentDim.copy(alpha = colors.ornamentDim.alpha * innerAlpha),
            Offset(inset, inset),
            Size(size.width - inset * 2, size.height - inset * 2),
            CornerRadius((radius.toPx() - inset).coerceAtLeast(0f)),
            style = Stroke(stroke),
        )
    }

    if (frame == TileFrame.chamber) {
        val gold = lerp(colors.ornamentDim, colors.focusRing, lift)
        val out = (3f + 3f * lift).dp.toPx()
        val arm = (11f + 7f * lift).dp.toPx()
        val cut = 4.dp.toPx()
        listOf(
            Offset(-out, -out) to Offset(1f, 1f),
            Offset(size.width + out, -out) to Offset(-1f, 1f),
            Offset(-out, size.height + out) to Offset(1f, -1f),
            Offset(size.width + out, size.height + out) to Offset(-1f, -1f),
        ).forEach { (corner, dir) -> bracket(corner, dir, arm, cut, gold, stroke * (1f + 0.4f * lift)) }
    }
}

/** One corner bracket at [corner], arms running along [dir] (each component +1 or -1). */
private fun DrawScope.bracket(corner: Offset, dir: Offset, arm: Float, cut: Float, color: Color, stroke: Float) {
    val path = Path().apply {
        moveTo(corner.x + dir.x * arm, corner.y)
        lineTo(corner.x + dir.x * cut, corner.y)
        lineTo(corner.x, corner.y + dir.y * cut)
        lineTo(corner.x, corner.y + dir.y * arm)
    }
    drawPath(path, color, style = Stroke(stroke, cap = StrokeCap.Square))
    // A small solid diamond just off the cut tip, pointing out of the corner.
    val d = 2.6.dp.toPx()
    val tip = Offset(corner.x - dir.x * d * 0.9f, corner.y - dir.y * d * 0.9f)
    rotate(45f, pivot = tip) {
        drawRect(color, Offset(tip.x - d / 2, tip.y - d / 2), Size(d, d))
    }
}
