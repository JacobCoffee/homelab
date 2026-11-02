package dev.jacobcoffee.hearth.ui.home

import android.content.Context
import android.content.Intent
import android.os.SystemClock
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
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
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.tv.material3.Text
import dev.jacobcoffee.hearth.media.NowPlaying
import dev.jacobcoffee.hearth.media.NowPlayingSource
import dev.jacobcoffee.hearth.media.formatTrackTime
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors
import dev.jacobcoffee.hearth.ui.toImageBitmap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

private val COVER = 46.dp

/** The media session worth showing, followed while Home is resumed; null when nothing plays. */
@Composable
fun rememberNowPlaying(source: NowPlayingSource): NowPlaying? {
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val state = remember { mutableStateOf<NowPlaying?>(null) }
    LaunchedEffect(source, lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) { source.updates().collect { state.value = it } }
    }
    return state.value
}

/**
 * Whatever another app is playing, in the home strip: cover, title, artist and app, a time
 * bar, and previous / play-pause / next. OK on the cover or title opens the player.
 */
@Composable
fun NowPlayingStrip(np: NowPlaying, source: NowPlayingSource, type: HomeType, modifier: Modifier = Modifier) {
    val colors = LocalHearthColors.current
    val context = LocalContext.current
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    Row(modifier.height(STRIP_HEIGHT), verticalAlignment = Alignment.CenterVertically) {
        Row(
            Modifier
                .weight(1f)
                .fillMaxHeight()
                .clickable(interactionSource = interaction, indication = null) { openPlayer(context, np) }
                .drawBehind {
                    if (!focused) return@drawBehind
                    val s = 1.dp.toPx()
                    drawRect(colors.accent.copy(alpha = 0.09f))
                    drawRect(colors.focusRing, Offset(s / 2, s / 2), Size(size.width - s, size.height - s), style = Stroke(s))
                }
                .padding(horizontal = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Cover(np, source)
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    displayText(np.title),
                    style = type.questTitle.copy(color = if (focused) colors.accent else colors.onSurface),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(3.dp))
                TrackProgress(np, type)
            }
        }
        Spacer(Modifier.width(10.dp))
        if (np.canPrevious) TransportButton(Sigil.Previous) { np.controls.skipToPrevious() }
        TransportButton(if (np.playing) Sigil.Pause else Sigil.Play) {
            if (np.playing) np.controls.pause() else np.controls.play()
        }
        if (np.canNext) TransportButton(Sigil.Next) { np.controls.skipToNext() }
    }
}

/** The player's own screen for this session if it offers one, else the app's TV entry. */
private fun openPlayer(context: Context, np: NowPlaying) {
    val sent = np.sessionActivity?.let { runCatching { it.send() }.isSuccess } ?: false
    if (sent) return
    val pm = context.packageManager
    val intent = pm.getLeanbackLaunchIntentForPackage(np.packageName) ?: pm.getLaunchIntentForPackage(np.packageName) ?: return
    context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}

/** Session art, else art fetched from the session's URI, else the player's icon. */
@Composable
private fun Cover(np: NowPlaying, source: NowPlayingSource) {
    val colors = LocalHearthColors.current
    val context = LocalContext.current
    val art by produceState<ImageBitmap?>(np.art?.asImageBitmap(), np.art, np.artUri, np.packageName) {
        value = np.art?.asImageBitmap()
            ?: np.artUri?.let { uri -> withContext(Dispatchers.IO) { source.loadArt(uri) } }?.asImageBitmap()
            ?: runCatching { context.packageManager.getApplicationIcon(np.packageName).toImageBitmap() }.getOrNull()
    }
    Box(Modifier.size(COVER).clip(RoundedCornerShape(3.dp)).background(colors.surface)) {
        art?.let { Image(it, contentDescription = null, modifier = Modifier.fillMaxSize(), contentScale = ContentScale.Crop) }
    }
}

/**
 * Artist and app, then a time bar with the time beside it. Sessions report position only on
 * changes, so while playing this ticks once a second from the last report, and only while
 * Home is resumed.
 */
@Composable
private fun TrackProgress(np: NowPlaying, type: HomeType) {
    val colors = LocalHearthColors.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val now by produceState(SystemClock.elapsedRealtime(), np) {
        value = SystemClock.elapsedRealtime()
        if (!np.playing) return@produceState
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            while (true) {
                value = SystemClock.elapsedRealtime()
                // Land on the next whole second of track time so the clock never skips one.
                delay((1000L - np.positionAt(value) % 1000L).coerceIn(50L, 1000L))
            }
        }
    }
    val position = np.positionAt(now)
    val time = when {
        np.durationMs > 0 -> "${formatTrackTime(position)} / ${formatTrackTime(np.durationMs)}"
        position > 0 -> formatTrackTime(position)
        else -> ""
    }
    Text(
        listOfNotNull(np.artist, np.appLabel).joinToString("  \u00b7  ").uppercase(),
        style = type.label,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
    )
    if (time.isEmpty()) return
    Spacer(Modifier.height(4.dp))
    Row(verticalAlignment = Alignment.CenterVertically) {
        if (np.durationMs > 0) {
            val fraction = (position.toFloat() / np.durationMs).coerceIn(0f, 1f)
            Canvas(Modifier.weight(1f).height(4.dp)) {
                val y = size.height / 2
                val track = 1.dp.toPx()
                val fill = 2.dp.toPx()
                drawLine(colors.line, Offset(0f, y), Offset(size.width, y), track)
                val x = size.width * fraction
                drawLine(colors.accent, Offset(0f, y), Offset(x, y), fill, StrokeCap.Round)
                drawRoundRect(colors.accent, Offset(x - fill, y - fill), Size(fill * 2, fill * 2), CornerRadius(fill))
            }
            Spacer(Modifier.width(10.dp))
        }
        Text(time, style = type.label)
    }
}

/** A round transport button: the drawn sigil turns to the accent inside a ring when focused. */
@Composable
private fun TransportButton(sigil: Sigil, onClick: () -> Unit) {
    val colors = LocalHearthColors.current
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    Box(
        Modifier
            .size(38.dp)
            .clickable(interactionSource = interaction, indication = null, onClick = onClick)
            .drawBehind {
                if (!focused) return@drawBehind
                val s = 1.dp.toPx()
                drawCircle(colors.accent.copy(alpha = 0.14f))
                drawCircle(colors.focusRing, radius = size.minDimension / 2 - s / 2, style = Stroke(s))
            },
        contentAlignment = Alignment.Center,
    ) {
        SigilIcon(sigil, if (focused) colors.accent else colors.onSurface, 18.dp)
    }
}
