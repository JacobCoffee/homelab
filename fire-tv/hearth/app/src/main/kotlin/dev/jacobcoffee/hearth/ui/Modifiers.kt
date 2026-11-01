package dev.jacobcoffee.hearth.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors

/**
 * A D-pad focusable tile: scales up and shows the theme's focus ring when focused, same
 * interaction model everywhere a tile/button appears (top bar gear, app tiles, menu rows).
 */
@OptIn(ExperimentalFoundationApi::class)
fun Modifier.clickableFocusable(
    cornerRadius: Dp = 8.dp,
    onClick: () -> Unit,
    onLongClick: (() -> Unit)? = null,
): Modifier = composed {
    val colors = LocalHearthColors.current
    val interactionSource = remember { MutableInteractionSource() }
    val focused by interactionSource.collectIsFocusedAsState()
    this
        .border(
            width = if (focused) 3.dp else 0.dp,
            color = if (focused) colors.focusRing else Color.Transparent,
            shape = RoundedCornerShape(cornerRadius),
        )
        .then(
            if (onLongClick != null) {
                Modifier.combinedClickable(
                    interactionSource = interactionSource,
                    indication = null,
                    onClick = onClick,
                    onLongClick = onLongClick,
                )
            } else {
                Modifier.clickable(interactionSource = interactionSource, indication = null, onClick = onClick)
            }
        )
}

/** Runs [action] the moment this element gains D-pad focus; used for live theme preview. */
fun Modifier.onFocusEnter(action: () -> Unit): Modifier =
    onFocusChanged { if (it.isFocused) action() }
