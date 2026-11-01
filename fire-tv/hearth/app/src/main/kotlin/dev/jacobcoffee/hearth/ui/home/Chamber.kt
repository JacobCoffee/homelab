package dev.jacobcoffee.hearth.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Text
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors

/**
 * The guild's one container: a night panel with a faint outer line and a second line 5dp
 * inside it, titled by a notched ribbon centered on the top edge. Colors come from the theme
 * (ribbon = surfaceFocused, inner line = ornamentDim), so every theme gets its own version.
 */
@Composable
fun Chamber(title: String, type: HomeType, modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val colors = LocalHearthColors.current
    Box(modifier) {
        Column(
            Modifier
                .padding(top = 18.dp)
                .background(Brush.verticalGradient(listOf(colors.surface.copy(alpha = 0.98f), colors.background.copy(alpha = 0.98f))))
                .drawBehind {
                    val s = 1.dp.toPx()
                    drawRect(colors.line, Offset(s / 2, s / 2), size.copy(size.width - s, size.height - s), style = Stroke(s))
                    val i = 5.dp.toPx()
                    drawRect(colors.ornamentDim, Offset(i, i), size.copy(size.width - i * 2, size.height - i * 2), style = Stroke(s))
                }
                .padding(start = 40.dp, end = 40.dp, top = 44.dp, bottom = 30.dp),
            content = content,
        )
        Ribbon(title, type, Modifier.align(Alignment.TopCenter))
    }
}

@Composable
private fun Ribbon(title: String, type: HomeType, modifier: Modifier) {
    val colors = LocalHearthColors.current
    Text(
        displayText(title),
        style = type.questTitle.copy(fontSize = 17.sp, shadow = null),
        modifier = modifier
            .offset(y = 2.dp)
            .drawBehind {
                val notch = 12.dp.toPx()
                val ribbon = Path().apply {
                    moveTo(notch, 0f); lineTo(size.width - notch, 0f); lineTo(size.width, size.height / 2)
                    lineTo(size.width - notch, size.height); lineTo(notch, size.height); lineTo(0f, size.height / 2); close()
                }
                drawPath(ribbon, colors.surfaceFocused)
                val s = 1.dp.toPx()
                drawLine(colors.ornamentDim, Offset(notch, s / 2), Offset(size.width - notch, s / 2), s)
                drawLine(colors.ornamentDim, Offset(notch, size.height - s / 2), Offset(size.width - notch, size.height - s / 2), s)
            }
            .padding(horizontal = 40.dp, vertical = 9.dp),
    )
}
