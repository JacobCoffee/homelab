package dev.jacobcoffee.hearth.ui.home

import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.lerp
import androidx.tv.material3.Text
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors

data class RailItem(val sigil: Sigil, val label: String, val onClick: () -> Unit)

/** Width the rail always occupies; the home content starts to its right. */
val RAIL_WIDTH: Dp = 60.dp
private val RAIL_OPEN_WIDTH = 260.dp

/** At rest the sigils are small, dim and close together; focus on the rail grows them. */
private val ITEM_HEIGHT_REST = 30.dp
private val ITEM_HEIGHT_OPEN = 46.dp
private val ICON_REST = 15.dp
private val ICON_OPEN = 24.dp
private const val ICON_ALPHA_REST = 0.45f
private val EaseOut = CubicBezierEasing(0.16f, 1f, 0.3f, 1f)

/**
 * A slim rail of drawn sigils on the left edge. Quiet at rest; when focus moves Left onto it,
 * it widens over the painting on a night gradient and names each sigil. Everything a remote
 * without Home or Menu still needs lives here.
 */
@Composable
fun CommandRail(items: List<RailItem>, type: HomeType, modifier: Modifier = Modifier, onOpenChanged: (Boolean) -> Unit = {}) {
    val colors = LocalHearthColors.current
    var open by remember { mutableStateOf(false) }
    val reveal by animateFloatAsState(if (open) 1f else 0f, tween(360, easing = EaseOut), label = "rail")

    Box(
        modifier
            .fillMaxHeight()
            .width(lerp(RAIL_WIDTH, RAIL_OPEN_WIDTH, reveal))
            .drawBehind {
                // Shade grows with the reveal so the names stay legible over the art.
                drawRect(
                    Brush.horizontalGradient(
                        0f to colors.background.copy(alpha = 0.22f + 0.75f * reveal),
                        0.62f to colors.background.copy(alpha = 0.10f + 0.82f * reveal),
                        1f to Color.Transparent,
                    )
                )
                // A gold hairline down the rail's edge, fading at both ends, while it is closed.
                if (reveal < 0.99f) {
                    val x = RAIL_WIDTH.toPx() - 1.dp.toPx()
                    drawLine(
                        Brush.verticalGradient(listOf(Color.Transparent, colors.ornamentDim, Color.Transparent), size.height * 0.25f, size.height * 0.75f),
                        Offset(x, size.height * 0.25f),
                        Offset(x, size.height * 0.75f),
                        1.dp.toPx(),
                        alpha = 1f - reveal,
                    )
                }
            },
    ) {
        Column(
            Modifier
                .align(Alignment.CenterStart)
                .fillMaxWidth()
                .onFocusChanged { if (open != it.hasFocus) { open = it.hasFocus; onOpenChanged(it.hasFocus) } },
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            items.forEach { RailEntry(it, type, reveal) }
        }
    }
}

@Composable
private fun RailEntry(item: RailItem, type: HomeType, reveal: Float) {
    val colors = LocalHearthColors.current
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    val glow = colors.focusLight ?: colors.accent
    Row(
        Modifier
            .fillMaxWidth()
            .height(lerp(ITEM_HEIGHT_REST, ITEM_HEIGHT_OPEN, reveal))
            .clickable(interactionSource = interaction, indication = null, onClick = item.onClick)
            .padding(start = 22.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        val icon = lerp(ICON_REST, ICON_OPEN, reveal)
        Box(
            Modifier.size(ICON_OPEN).drawBehind {
                if (focused) {
                    drawCircle(
                        Brush.radialGradient(listOf(glow.copy(alpha = 0.45f), Color.Transparent), center, size.minDimension * 1.3f),
                        size.minDimension * 1.3f,
                    )
                }
            },
            contentAlignment = Alignment.Center,
        ) {
            SigilIcon(
                item.sigil,
                if (focused) colors.accent else colors.onSurfaceMuted,
                size = icon,
                modifier = Modifier.graphicsLayer { alpha = ICON_ALPHA_REST + (1f - ICON_ALPHA_REST) * reveal },
            )
        }
        Spacer(Modifier.width(22.dp))
        Text(
            item.label.uppercase(),
            style = type.label.copy(color = if (focused) colors.accent else colors.onSurface),
            maxLines = 1,
            modifier = Modifier.graphicsLayer { alpha = reveal },
        )
    }
}
