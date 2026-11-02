package dev.jacobcoffee.hearth.ui.home

import android.graphics.BitmapFactory
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathOperation
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Text
import dev.jacobcoffee.hearth.config.HearthConfig
import dev.jacobcoffee.hearth.config.WeatherConfig
import dev.jacobcoffee.hearth.theme.ThemeAssets
import dev.jacobcoffee.hearth.theme.ThemeGreetings
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors
import dev.jacobcoffee.hearth.ui.theme.LocalHearthTheme
import dev.jacobcoffee.hearth.widgets.TvLink
import dev.jacobcoffee.hearth.widgets.WeatherRepository
import dev.jacobcoffee.hearth.widgets.WeatherState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle as JavaTextStyle
import java.util.Locale

@Composable
fun HomeHeader(config: HearthConfig, type: HomeType, modifier: Modifier = Modifier) {
    val theme = LocalHearthTheme.current
    val now = rememberMinute()
    val greeting = greetingFor(theme.json.greetings, now.hour, config.greetingName)

    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Crest()
        Column(Modifier.weight(1f)) {
            Text(displayText(greeting), style = type.greeting, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(10.dp))
            config.weather?.let { Almanac(it, type) }
            if (config.uptimeKuma != null || config.adguard != null) {
                Spacer(Modifier.height(9.dp))
                Watchtower(config, type)
            }
        }
        Spacer(Modifier.width(24.dp))
        Column(horizontalAlignment = Alignment.End) {
            Row(verticalAlignment = Alignment.Bottom) {
                Text(now.format(DateTimeFormatter.ofPattern("h:mm")), style = type.clock)
                Spacer(Modifier.width(6.dp))
                Text(now.format(DateTimeFormatter.ofPattern("a")).uppercase(), style = type.label, modifier = Modifier.padding(bottom = 7.dp))
            }
            Spacer(Modifier.height(4.dp))
            Text(
                "${now.dayOfWeek.getDisplayName(JavaTextStyle.FULL, Locale.getDefault())}  ·  ${now.format(DateTimeFormatter.ofPattern("MMMM d"))}".uppercase(),
                style = type.label,
            )
            // The sleep timer set from the rail's Power panel; the clock recomposes each minute.
            TvLink.sleepUntil(LocalContext.current)?.let { until ->
                val minutes = ((until - System.currentTimeMillis()) / 60_000L).coerceAtLeast(1)
                Spacer(Modifier.height(6.dp))
                Text("TV OFF IN $minutes MIN", style = type.label.copy(color = LocalHearthColors.current.accent))
            }
        }
    }
}

/** The theme's logo, when it has one, sized to sit beside the greeting. */
@Composable
private fun Crest() {
    val context = LocalContext.current
    val theme = LocalHearthTheme.current
    val logo by produceState<ImageBitmap?>(null, theme) {
        value = theme.json.logo?.let { ThemeAssets.resolve(context, theme, it) }?.let { file ->
            withContext(Dispatchers.IO) { BitmapFactory.decodeFile(file.path)?.asImageBitmap() }
        }
    }
    logo?.let {
        Image(it, contentDescription = null, modifier = Modifier.height(84.dp).padding(end = 22.dp), contentScale = ContentScale.Fit)
    }
}

/** Ticks on each minute boundary, not every second: the clock shows minutes. */
@Composable
private fun rememberMinute(): LocalDateTime {
    var now by remember { mutableStateOf(LocalDateTime.now()) }
    LaunchedEffect(Unit) {
        while (true) {
            val current = LocalDateTime.now()
            now = current
            delay(((60 - current.second) * 1000L - current.nano / 1_000_000L).coerceAtLeast(250L))
        }
    }
    return now
}

private fun greetingFor(greetings: ThemeGreetings?, hour: Int, name: String): String {
    val template = when (hour) {
        in 5..11 -> greetings?.morning ?: "Good morning, {name}"
        in 12..16 -> greetings?.afternoon ?: "Good afternoon, {name}"
        in 17..20 -> greetings?.evening ?: "Good evening, {name}"
        else -> greetings?.night ?: "Good night, {name}"
    }
    return template.replace("{name}", name)
}

/** Weather as a line of prose with drawn symbols: now, then the next two days. */
@Composable
private fun Almanac(config: WeatherConfig, type: HomeType) {
    val colors = LocalHearthColors.current
    val repo = remember { WeatherRepository() }
    val state by rememberPolled(
        key = config,
        initial = repo.cached(config.lat, config.lon, config.units),
        periodMs = 20 * 60 * 1000L,
        retryMs = 60_000L,
        failed = { it !is WeatherState.Available || it.stale },
    ) {
        repo.fetch(config.lat, config.lon, config.units)
    }
    val s = state as? WeatherState.Available ?: return
    val snap = s.snapshot
    val place = config.label.substringBefore(',')
    Row(verticalAlignment = Alignment.CenterVertically) {
        WeatherGlyph(snap.weatherCode, snap.isDay, colors.onSurface, 22.dp)
        Spacer(Modifier.width(10.dp))
        Text("${snap.currentTempF.toInt()}°", style = type.body)
        Text("  ${weatherWord(snap.weatherCode)} in $place", style = type.bodyMuted)
        Row(horizontalArrangement = Arrangement.spacedBy(18.dp), modifier = Modifier.padding(start = 26.dp)) {
            snap.daily.drop(1).take(2).forEach { d ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(dayAbbrev(d.date), style = type.label)
                    Spacer(Modifier.width(7.dp))
                    WeatherGlyph(d.weatherCode, true, colors.onSurfaceMuted, 15.dp)
                    Spacer(Modifier.width(7.dp))
                    Text("${d.maxF.toInt()}°  ${d.minF.toInt()}°", style = type.label)
                }
            }
        }
        if (s.stale) {
            val asOf = java.time.Instant.ofEpochMilli(snap.fetchedAtMillis).atZone(java.time.ZoneId.systemDefault())
            Text("  ·  as of ${asOf.format(DateTimeFormatter.ofPattern("h:mm a"))}", style = type.aside, maxLines = 1, softWrap = false)
        }
    }
}

private fun dayAbbrev(isoDate: String): String = runCatching {
    LocalDate.parse(isoDate).dayOfWeek.getDisplayName(JavaTextStyle.SHORT, Locale.getDefault()).uppercase()
}.getOrDefault("")

private fun weatherWord(code: Int): String = when (code) {
    0 -> "Clear"
    1 -> "Mostly clear"
    2 -> "Partly cloudy"
    3 -> "Overcast"
    45, 48 -> "Fog"
    in 51..57 -> "Drizzle"
    in 61..67 -> "Rain"
    in 71..77 -> "Snow"
    in 80..82 -> "Showers"
    85, 86 -> "Snow showers"
    in 95..99 -> "Thunderstorms"
    else -> "Fair"
}

private enum class Sky { CLEAR, PARTLY, CLOUD, FOG, RAIN, SNOW, STORM }

private fun skyFor(code: Int): Sky = when (code) {
    0, 1 -> Sky.CLEAR
    2 -> Sky.PARTLY
    3 -> Sky.CLOUD
    45, 48 -> Sky.FOG
    in 51..67, in 80..82 -> Sky.RAIN
    in 71..77, 85, 86 -> Sky.SNOW
    in 95..99 -> Sky.STORM
    else -> Sky.PARTLY
}

/** Line-drawn weather symbols in one stroke weight, tinted from the theme's text color. */
@Composable
private fun WeatherGlyph(code: Int, isDay: Boolean, color: Color, size: Dp) {
    Canvas(Modifier.size(size)) {
        val u = this.size.minDimension / 20f
        val stroke = Stroke(width = 1.4f * u * (20f / 20f), cap = StrokeCap.Round)
        when (skyFor(code)) {
            Sky.CLEAR -> if (isDay) sun(Offset(10 * u, 10 * u), 4 * u, color, stroke, rays = 8) else moon(u, color, stroke)
            Sky.PARTLY -> {
                if (isDay) sun(Offset(7 * u, 7 * u), 3 * u, color, stroke, rays = 5) else moon(u * 0.7f, color, stroke)
                cloud(Rect(Offset(4.5f * u, 8f * u), Size(14f * u, 8f * u)), color, stroke)
            }
            Sky.CLOUD -> cloud(Rect(Offset(2f * u, 5f * u), Size(16f * u, 10f * u)), color, stroke)
            Sky.FOG -> listOf(6f to 16f, 10f to 13f, 14f to 17f).forEach { (y, len) ->
                drawLine(color, Offset((20 - len) / 2 * u, y * u), Offset((20 + len) / 2 * u, y * u), stroke.width, StrokeCap.Round)
            }
            Sky.RAIN -> {
                cloud(Rect(Offset(2f * u, 2f * u), Size(16f * u, 9.5f * u)), color, stroke)
                listOf(6f, 10f, 14f).forEach { x -> drawLine(color, Offset(x * u, 14f * u), Offset((x - 1.5f) * u, 18f * u), stroke.width, StrokeCap.Round) }
            }
            Sky.SNOW -> {
                cloud(Rect(Offset(2f * u, 2f * u), Size(16f * u, 9.5f * u)), color, stroke)
                listOf(6f to 15f, 10f to 17.5f, 14f to 15f).forEach { (x, y) -> drawCircle(color, 1.1f * u, Offset(x * u, y * u)) }
            }
            Sky.STORM -> {
                cloud(Rect(Offset(2f * u, 2f * u), Size(16f * u, 9.5f * u)), color, stroke)
                val bolt = Path().apply {
                    moveTo(11f * u, 12.5f * u); lineTo(8.5f * u, 16f * u); lineTo(11f * u, 16f * u); lineTo(9f * u, 19.5f * u)
                }
                drawPath(bolt, color, style = stroke)
            }
        }
    }
}

private fun DrawScope.sun(center: Offset, r: Float, color: Color, stroke: Stroke, rays: Int) {
    drawCircle(color, r, center, style = stroke)
    val inner = r + 2.2f * (r / 4f)
    val outer = inner + 2.4f * (r / 4f)
    val sweep = if (rays >= 8) 360f else 200f
    val start = if (rays >= 8) 0f else 150f
    for (i in 0 until rays) {
        val a = Math.toRadians((start + sweep * i / (if (rays >= 8) rays else rays - 1)).toDouble())
        val dx = kotlin.math.cos(a).toFloat()
        val dy = kotlin.math.sin(a).toFloat()
        drawLine(color, Offset(center.x + dx * inner, center.y + dy * inner), Offset(center.x + dx * outer, center.y + dy * outer), stroke.width, StrokeCap.Round)
    }
}

private fun DrawScope.moon(u: Float, color: Color, stroke: Stroke) {
    val outer = Path().apply { addOval(Rect(Offset(10 * u, 10 * u), 6 * u)) }
    val bite = Path().apply { addOval(Rect(Offset(13.5f * u, 7.5f * u), 5.2f * u)) }
    drawPath(Path.combine(PathOperation.Difference, outer, bite), color, style = stroke)
}

/** A cloud outline inside [box]: three overlapping lobes over a flat base, merged into one path. */
private fun DrawScope.cloud(box: Rect, color: Color, stroke: Stroke) {
    val w = box.width
    val h = box.height
    val base = box.top + h
    val lobes = listOf(
        Rect(Offset(box.left + w * 0.24f, base - h * 0.36f), h * 0.36f),
        Rect(Offset(box.left + w * 0.52f, base - h * 0.58f), h * 0.48f),
        Rect(Offset(box.left + w * 0.78f, base - h * 0.34f), h * 0.32f),
    )
    var shape = Path().apply { addRect(Rect(box.left + w * 0.12f, base - h * 0.36f, box.left + w * 0.9f, base)) }
    lobes.forEach { r -> shape = Path.combine(PathOperation.Union, shape, Path().apply { addOval(r) }) }
    drawPath(shape, color, style = stroke)
}
