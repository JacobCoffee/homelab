package dev.jacobcoffee.hearth.ui.home

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlin.math.cos
import kotlin.math.sin

/** The home screen's drawn symbols: one line family, one stroke weight, on a 24-unit grid. */
enum class Sigil { Search, Arrange, Apps, Inputs, TvSettings, Controllers, Power, Homelab, Theme, Settings, Previous, Play, Pause, Next }

@Composable
fun SigilIcon(sigil: Sigil, color: Color, size: Dp = 24.dp, modifier: Modifier = Modifier) {
    Canvas(modifier.size(size)) {
        val u = this.size.minDimension / 24f
        val stroke = Stroke(width = 1.6f * u, cap = StrokeCap.Round, join = StrokeJoin.Round)
        fun p(x: Float, y: Float) = Offset(x * u, y * u)
        fun line(x1: Float, y1: Float, x2: Float, y2: Float) = drawLine(color, p(x1, y1), p(x2, y2), stroke.width, StrokeCap.Round)
        fun path(block: Path.() -> Unit) = drawPath(Path().apply(block), color, style = stroke)
        fun circle(cx: Float, cy: Float, r: Float) = drawCircle(color, r * u, p(cx, cy), style = stroke)
        fun diamond(cx: Float, cy: Float, r: Float) = path {
            moveTo(cx * u, (cy - r) * u); lineTo((cx + r) * u, cy * u); lineTo(cx * u, (cy + r) * u); lineTo((cx - r) * u, cy * u); close()
        }
        when (sigil) {
            Sigil.Search -> { circle(10.5f, 10.5f, 6.5f); line(15.3f, 15.3f, 20.5f, 20.5f) }
            Sigil.Arrange -> {
                line(12f, 2.5f, 12f, 21.5f); line(2.5f, 12f, 21.5f, 12f)
                listOf(Offset(0f, -1f), Offset(0f, 1f), Offset(-1f, 0f), Offset(1f, 0f)).forEach { d ->
                    val tipX = 12f + d.x * 9.5f
                    val tipY = 12f + d.y * 9.5f
                    line(tipX, tipY, tipX - d.x * 2.6f + d.y * 2.6f, tipY - d.y * 2.6f + d.x * 2.6f)
                    line(tipX, tipY, tipX - d.x * 2.6f - d.y * 2.6f, tipY - d.y * 2.6f - d.x * 2.6f)
                }
                diamond(12f, 12f, 2.8f)
            }
            Sigil.Apps -> { diamond(7f, 7f, 3.5f); diamond(17f, 7f, 3.5f); diamond(7f, 17f, 3.5f); diamond(17f, 17f, 3.5f) }
            Sigil.Inputs -> {
                drawRoundRectLine(color, p(3f, 4.5f), Size(18f * u, 12f * u), stroke)
                line(9f, 20f, 15f, 20f)
                path { moveTo(9.5f * u, 10.5f * u); lineTo(14.5f * u, 10.5f * u); moveTo(12.5f * u, 8.5f * u); lineTo(14.5f * u, 10.5f * u); lineTo(12.5f * u, 12.5f * u) }
            }
            Sigil.Controllers -> {
                // A gamepad: rounded body, d-pad cross on the left, two buttons on the right.
                path {
                    moveTo(7f * u, 7f * u); lineTo(17f * u, 7f * u)
                    cubicTo(21f * u, 7f * u, 22.5f * u, 12f * u, 22f * u, 16f * u)
                    cubicTo(21.6f * u, 18.8f * u, 18.8f * u, 19.4f * u, 17.2f * u, 17f * u)
                    lineTo(15.5f * u, 15f * u); lineTo(8.5f * u, 15f * u); lineTo(6.8f * u, 17f * u)
                    cubicTo(5.2f * u, 19.4f * u, 2.4f * u, 18.8f * u, 2f * u, 16f * u)
                    cubicTo(1.5f * u, 12f * u, 3f * u, 7f * u, 7f * u, 7f * u); close()
                }
                line(7f, 9.6f, 7f, 13.4f); line(5.1f, 11.5f, 8.9f, 11.5f)
                drawCircle(color, 1.1f * u, p(16.2f, 10.2f)); drawCircle(color, 1.1f * u, p(18.4f, 12.6f))
            }
            Sigil.TvSettings -> {
                drawRoundRectLine(color, p(2.5f, 4f), Size(19f * u, 13f * u), stroke)
                line(9f, 20.5f, 15f, 20.5f)
                circle(12f, 10.5f, 2.2f)
                for (i in 0 until 6) {
                    val a = Math.toRadians(i * 60.0)
                    line(12f + cos(a).toFloat() * 3.6f, 10.5f + sin(a).toFloat() * 3.6f, 12f + cos(a).toFloat() * 4.8f, 10.5f + sin(a).toFloat() * 4.8f)
                }
            }
            Sigil.Power -> {
                drawArc(color, -60f, 300f, false, p(4.5f, 4.5f), Size(15f * u, 15f * u), style = stroke)
                line(12f, 2.8f, 12f, 11.5f)
            }
            Sigil.Homelab -> {
                // A watchtower: tapering tower, a beacon at the top.
                path { moveTo(8f * u, 21f * u); lineTo(9.5f * u, 9f * u); lineTo(14.5f * u, 9f * u); lineTo(16f * u, 21f * u) }
                path { moveTo(7.5f * u, 9f * u); lineTo(16.5f * u, 9f * u); lineTo(16.5f * u, 6.5f * u); lineTo(7.5f * u, 6.5f * u); close() }
                diamond(12f, 3.2f, 1.6f)
                line(10f, 14f, 14f, 14f)
            }
            Sigil.Theme -> {
                // A crescent moon over a four-point star: the scene changes with the season.
                path {
                    addArc(Rect(p(3.5f, 3.5f), Size(13f * u, 13f * u)), 90f, 250f)
                }
                val sx = 17f
                val sy = 16f
                path {
                    moveTo(sx * u, (sy - 4.5f) * u); quadraticBezierTo((sx + 0.9f) * u, (sy - 0.9f) * u, (sx + 4.5f) * u, sy * u)
                    quadraticBezierTo((sx + 0.9f) * u, (sy + 0.9f) * u, sx * u, (sy + 4.5f) * u)
                    quadraticBezierTo((sx - 0.9f) * u, (sy + 0.9f) * u, (sx - 4.5f) * u, sy * u)
                    quadraticBezierTo((sx - 0.9f) * u, (sy - 0.9f) * u, sx * u, (sy - 4.5f) * u); close()
                }
            }
            Sigil.Settings -> {
                circle(12f, 12f, 3.4f)
                circle(12f, 12f, 6.6f)
                for (i in 0 until 8) {
                    val a = Math.toRadians(i * 45.0)
                    line(12f + cos(a).toFloat() * 6.6f, 12f + sin(a).toFloat() * 6.6f, 12f + cos(a).toFloat() * 9.2f, 12f + sin(a).toFloat() * 9.2f)
                }
            }
            Sigil.Play -> path { moveTo(8f * u, 5f * u); lineTo(19f * u, 12f * u); lineTo(8f * u, 19f * u); close() }
            Sigil.Pause -> { line(8.5f, 5.5f, 8.5f, 18.5f); line(15.5f, 5.5f, 15.5f, 18.5f) }
            Sigil.Next -> {
                path { moveTo(5f * u, 6f * u); lineTo(14.5f * u, 12f * u); lineTo(5f * u, 18f * u); close() }
                line(18.5f, 6f, 18.5f, 18f)
            }
            Sigil.Previous -> {
                path { moveTo(19f * u, 6f * u); lineTo(9.5f * u, 12f * u); lineTo(19f * u, 18f * u); close() }
                line(5.5f, 6f, 5.5f, 18f)
            }
        }
    }
}

private fun DrawScope.drawRoundRectLine(color: Color, topLeft: Offset, size: Size, stroke: Stroke) {
    drawRoundRect(color, topLeft, size, androidx.compose.ui.geometry.CornerRadius(1.5f * stroke.width), style = stroke)
}
