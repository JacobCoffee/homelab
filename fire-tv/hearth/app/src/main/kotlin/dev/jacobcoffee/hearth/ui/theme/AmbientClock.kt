package dev.jacobcoffee.hearth.ui.theme

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.delay

/**
 * Frame time in nanoseconds for every ambient motion (particles, wallpaper drift). One clock
 * means the screen is redrawn once per tick however many effects read it. Null when motion is
 * off. Read it only in draw or graphicsLayer lambdas so a tick never recomposes anything.
 */
val LocalAmbientClock = staticCompositionLocalOf<State<Long>?> { null }

/**
 * Ambient motion drifts a few percent of the screen per second, so 30 fps reads as smooth.
 * Waiting this long after a frame makes the next withFrameNanos land on the second vsync.
 */
private const val TICK_GAP_MS = 17L

/** Ticks only while the activity is resumed: nothing runs behind an app the user opened. */
@Composable
fun rememberAmbientClock(enabled: Boolean): State<Long>? {
    val clock = remember { mutableLongStateOf(0L) }
    if (!enabled) return null
    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(lifecycleOwner) {
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            while (true) {
                withFrameNanos { clock.longValue = it }
                delay(TICK_GAP_MS)
            }
        }
    }
    return clock
}
