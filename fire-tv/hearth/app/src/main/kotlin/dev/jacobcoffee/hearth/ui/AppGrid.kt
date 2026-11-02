package dev.jacobcoffee.hearth.ui

import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.boundsInRoot
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.min
import androidx.tv.material3.Text
import dev.jacobcoffee.hearth.apps.AppArt
import dev.jacobcoffee.hearth.apps.AppInfo
import dev.jacobcoffee.hearth.ui.home.HomeType
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors
import dev.jacobcoffee.hearth.ui.theme.LocalHearthTheme
import dev.jacobcoffee.hearth.ui.theme.LocalHearthTileRadius
import kotlin.math.ceil

/** Decelerating ease: arrives fast, settles gently. */
val EaseOutExpo = CubicBezierEasing(0.16f, 1f, 0.3f, 1f)

private val GAP_X = 20.dp
private val GAP_Y = 8.dp
/** Room under each tile for the focused app's name. */
private val LABEL_H = 22.dp
private val MAX_TILE_W = 232.dp
private const val FOCUS_SCALE = 0.06f

private data class GridFit(val cols: Int, val tileW: Dp, val tileH: Dp)

/** The largest 16:9 tile whose rows all fit [maxW] x [maxH]: every app visible, no scrolling. */
private fun fitGrid(count: Int, maxW: Dp, maxH: Dp): GridFit {
    var best: GridFit? = null
    for (cols in 3..9) {
        val tileW = min(MAX_TILE_W, (maxW - GAP_X * (cols - 1)) / cols)
        val tileH = tileW * 9f / 16f
        val rows = ceil(count / cols.toFloat()).toInt().coerceAtLeast(1)
        val height = (tileH + LABEL_H) * rows + GAP_Y * (rows - 1)
        if (height <= maxH && (best == null || tileW > best.tileW)) best = GridFit(cols, tileW, tileH)
    }
    return best ?: run {
        val cols = 9
        val tileW = (maxW - GAP_X * (cols - 1)) / cols
        GridFit(cols, tileW, tileW * 9f / 16f)
    }
}

/**
 * Every launchable TV app as a grid that fits the space it is given. [onFocusedBounds] reports
 * a tile's bounds in root coordinates when it gains focus and null when it loses it, keyed by
 * package, so the screen can light the painting beneath whichever tile holds focus.
 * [upFromFirstRow], when set, is where Up goes from the top row; otherwise focus search decides,
 * which from the left-hand tiles finds the rail.
 */
@Composable
fun AppGrid(
    apps: List<AppInfo>,
    type: HomeType,
    firstTileFocus: FocusRequester,
    upFromFirstRow: FocusRequester?,
    onLaunch: (AppInfo) -> Unit,
    onMenu: (AppInfo) -> Unit,
    onFocusedBounds: (packageName: String, bounds: Rect?) -> Unit,
    modifier: Modifier = Modifier,
) {
    BoxWithConstraints(modifier, contentAlignment = Alignment.Center) {
        val fit = remember(apps.size, maxWidth, maxHeight) { fitGrid(apps.size, maxWidth, maxHeight) }
        Column(verticalArrangement = Arrangement.spacedBy(GAP_Y)) {
            apps.chunked(fit.cols).forEachIndexed { row, rowApps ->
                Row(horizontalArrangement = Arrangement.spacedBy(GAP_X)) {
                    rowApps.forEachIndexed { col, app ->
                        AppTile(
                            app = app,
                            width = fit.tileW,
                            height = fit.tileH,
                            type = type,
                            focusRequester = if (row == 0 && col == 0) firstTileFocus else null,
                            upFocus = if (row == 0) upFromFirstRow else null,
                            onClick = { onLaunch(app) },
                            onLongClick = { onMenu(app) },
                            onFocusChanged = { bounds -> onFocusedBounds(app.packageName, bounds) },
                        )
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun AppTile(
    app: AppInfo,
    width: Dp,
    height: Dp,
    type: HomeType,
    focusRequester: FocusRequester?,
    upFocus: FocusRequester?,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
    onFocusChanged: (Rect?) -> Unit,
) {
    val colors = LocalHearthColors.current
    val radius = LocalHearthTileRadius.current
    val frame = TileFrame.of(LocalHearthTheme.current.json.tile?.frame)
    val context = LocalContext.current
    // Read at the tile's focused (largest) width, so focus never upscales the art.
    val bannerWidthPx = with(LocalDensity.current) { (width * (1f + FOCUS_SCALE)).roundToPx() }
    val banner = remember(app, bannerWidthPx) { AppArt.banner(context, app, bannerWidthPx) }
    val icon = remember(app, banner) { if (banner == null) AppArt.icon(context, app) else null }
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    val lift by animateFloatAsState(if (focused) 1f else 0f, tween(320, easing = EaseOutExpo), label = "lift")
    var bounds by remember { mutableStateOf<Rect?>(null) }

    LaunchedEffect(focused, bounds) {
        if (focused) onFocusChanged(bounds) else if (bounds != null) onFocusChanged(null)
    }

    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.width(width)) {
        Box(
            modifier = Modifier
                .size(width, height)
                .onGloballyPositioned { bounds = it.boundsInRoot() }
                .graphicsLayer {
                    val s = 1f + FOCUS_SCALE * lift
                    scaleX = s
                    scaleY = s
                }
                .then(if (focusRequester != null) Modifier.focusRequester(focusRequester) else Modifier)
                .then(if (upFocus != null) Modifier.focusProperties { up = upFocus } else Modifier)
                .onKeyEvent { e ->
                    if (e.key == Key.Menu && e.type == KeyEventType.KeyDown) { onLongClick(); true } else false
                }
                .combinedClickable(interactionSource = interaction, indication = null, onClick = onClick, onLongClick = onLongClick)
                .drawWithContent {
                    drawContent()
                    drawTileFrame(frame, lift, colors, radius)
                },
        ) {
            if (banner != null) {
                Image(
                    bitmap = banner,
                    contentDescription = app.label,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize().clip(RoundedCornerShape(radius)),
                )
            } else {
                Column(
                    modifier = Modifier.fillMaxSize().background(colors.surface, RoundedCornerShape(radius)),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    icon?.let { Image(bitmap = it, contentDescription = null, modifier = Modifier.size(height * 0.42f)) }
                    Spacer(Modifier.height(8.dp))
                    Text(
                        app.label,
                        style = type.bodyMuted.copy(fontSize = type.label.fontSize * 1.25f, shadow = null),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.padding(horizontal = 10.dp),
                    )
                }
            }
        }
        Box(Modifier.height(LABEL_H).padding(top = 9.dp), contentAlignment = Alignment.TopCenter) {
            Text(
                app.label.uppercase(),
                style = type.label.copy(color = colors.accent),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.graphicsLayer { alpha = lift },
            )
        }
    }
}
