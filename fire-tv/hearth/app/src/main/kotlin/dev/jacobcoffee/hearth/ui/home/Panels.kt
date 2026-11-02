package dev.jacobcoffee.hearth.ui.home

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Text
import dev.jacobcoffee.hearth.apps.AppArt
import dev.jacobcoffee.hearth.apps.AppInfo
import dev.jacobcoffee.hearth.config.AdGuardConfig
import dev.jacobcoffee.hearth.config.TvConfig
import dev.jacobcoffee.hearth.config.UptimeKumaConfig
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors
import dev.jacobcoffee.hearth.widgets.AdGuardRepository
import dev.jacobcoffee.hearth.widgets.AdGuardState
import dev.jacobcoffee.hearth.widgets.KumaMonitor
import dev.jacobcoffee.hearth.widgets.KumaState
import dev.jacobcoffee.hearth.widgets.TvLink
import dev.jacobcoffee.hearth.widgets.UptimeKumaRepository
import kotlinx.coroutines.launch
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** A chamber over a night veil; Back closes it. The first entry inside should request focus. */
@Composable
fun PanelOverlay(title: String, type: HomeType, onDismiss: () -> Unit, content: @Composable () -> Unit) {
    val colors = LocalHearthColors.current
    BackHandler(onBack = onDismiss)
    Box(Modifier.fillMaxSize().background(colors.background.copy(alpha = 0.74f)), contentAlignment = Alignment.Center) {
        Chamber(title, type, Modifier.widthIn(min = 480.dp, max = 760.dp)) { content() }
    }
}

/** Focuses [requester] once the panel is composed. */
@Composable
private fun FocusOnOpen(requester: FocusRequester) {
    LaunchedEffect(Unit) { runCatching { requester.requestFocus() } }
}

/** A focusable row: a diamond marks focus, the label turns to the accent. */
@Composable
fun PanelRow(label: String, type: HomeType, onClick: () -> Unit, modifier: Modifier = Modifier, detail: String? = null) {
    val colors = LocalHearthColors.current
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    Row(
        modifier
            .fillMaxWidth()
            .clickable(interactionSource = interaction, indication = null, onClick = onClick)
            .drawBehind { if (focused) drawRect(colors.accent.copy(alpha = 0.09f)) }
            .padding(vertical = 11.dp, horizontal = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.width(20.dp)) { if (focused) Diamond(colors.accent, 6.dp) }
        Text(label, style = type.body.copy(color = if (focused) colors.accent else colors.onSurface, shadow = null), modifier = Modifier.weight(1f))
        if (detail != null) Text(detail.uppercase(), style = type.label.copy(shadow = null))
    }
}

// ------------------------------------------------------------------ apps

/** Fire OS's own launchers (Amazon Home, Silk, Appstore, ...) as icon tiles. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AppsPanel(apps: List<AppInfo>, type: HomeType, onLaunch: (AppInfo) -> Unit, onDismiss: () -> Unit) {
    val first = remember { FocusRequester() }
    PanelOverlay("Apps", type, onDismiss) {
        FlowRow(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            apps.forEachIndexed { i, app ->
                IconTile(app, type, { onLaunch(app) }, if (i == 0) Modifier.focusRequester(first) else Modifier)
            }
        }
        if (apps.isNotEmpty()) FocusOnOpen(first)
    }
}

@Composable
private fun IconTile(app: AppInfo, type: HomeType, onClick: () -> Unit, modifier: Modifier) {
    val colors = LocalHearthColors.current
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    val context = LocalContext.current
    val icon = remember(app) { AppArt.icon(context, app) }
    Column(
        modifier
            .width(118.dp)
            .clickable(interactionSource = interaction, indication = null, onClick = onClick)
            .drawBehind {
                val s = 1.dp.toPx()
                if (focused) drawRect(colors.accent.copy(alpha = 0.08f))
                drawRect(if (focused) colors.focusRing else colors.line.copy(alpha = 0f), Offset(s / 2, s / 2), size.copy(size.width - s, size.height - s), style = Stroke(s))
            }
            .padding(vertical = 14.dp, horizontal = 8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Image(icon, contentDescription = null, modifier = Modifier.size(48.dp))
        Spacer(Modifier.height(10.dp))
        Text(
            app.label.uppercase(),
            style = type.label.copy(color = if (focused) colors.accent else colors.onSurfaceMuted, shadow = null),
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.Center,
        )
    }
}

// ------------------------------------------------------------------ TV

/** Sends [word] to the TV; on failure the panel says so instead of silently doing nothing. */
@Composable
private fun rememberTvSender(tv: TvConfig, after: () -> Unit): Pair<(String) -> Unit, String?> {
    val scope = rememberCoroutineScope()
    var error by remember { mutableStateOf<String?>(null) }
    val send: (String) -> Unit = { word ->
        scope.launch {
            if (TvLink.send(tv, word)) { error = null; after() }
            else error = "The TV didn't answer. Run tcl-tv/bridge/bridge.sh start on the Mac, then try again."
        }
    }
    return send to error
}

@Composable
fun InputsPanel(tv: TvConfig, type: HomeType, onDismiss: () -> Unit) {
    val first = remember { FocusRequester() }
    val (send, error) = rememberTvSender(tv, onDismiss)
    PanelOverlay("Inputs", type, onDismiss) {
        PanelRow("TV home", type, { send("tvhome") }, Modifier.focusRequester(first))
        (1..4).forEach { n -> PanelRow("HDMI $n", type, { send("hdmi$n") }, detail = if (n == 2) "This stick" else null) }
        error?.let { Text(it, style = type.aside.copy(color = LocalHearthColors.current.warning, shadow = null), modifier = Modifier.padding(top = 12.dp)) }
        FocusOnOpen(first)
    }
}

@Composable
fun PowerPanel(tv: TvConfig, type: HomeType, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val first = remember { FocusRequester() }
    val sleepUntil = remember { TvLink.sleepUntil(context) }
    val (send, error) = rememberTvSender(tv, onDismiss)
    fun sleepIn(minutes: Int) {
        send("sleep:$minutes")
        TvLink.rememberSleep(context, System.currentTimeMillis() + minutes * 60_000L)
    }
    PanelOverlay("Power", type, onDismiss) {
        PanelRow("Turn the TV off now", type, { TvLink.rememberSleep(context, null); send("off") }, Modifier.focusRequester(first))
        listOf(30, 60, 90).forEach { m -> PanelRow("Turn the TV off in $m minutes", type, { sleepIn(m) }) }
        if (sleepUntil != null) {
            val at = Instant.ofEpochMilli(sleepUntil).atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("h:mm a"))
            PanelRow("Cancel the sleep timer", type, { TvLink.rememberSleep(context, null); send("sleep:cancel") }, detail = "Off at $at")
        }
        error?.let { Text(it, style = type.aside.copy(color = LocalHearthColors.current.warning, shadow = null), modifier = Modifier.padding(top = 12.dp)) }
        FocusOnOpen(first)
    }
}

// ------------------------------------------------------------------ homelab

/** Every monitor with its state and how long it has held it, plus AdGuard's day so far. */
@Composable
fun HomelabPanel(kuma: UptimeKumaConfig?, adguard: AdGuardConfig?, type: HomeType, onDismiss: () -> Unit) {
    val colors = LocalHearthColors.current
    val kumaRepo = remember { UptimeKumaRepository() }
    val adRepo = remember { AdGuardRepository() }
    val kumaState by rememberPolled(kuma, KumaState.Loading as KumaState, 30_000L) {
        if (kuma == null) KumaState.Unavailable else kumaRepo.fetch(kuma.baseUrl, kuma.statusPageSlug)
    }
    val adState by rememberPolled(adguard, AdGuardState.Loading as AdGuardState, 30_000L) {
        if (adguard == null) AdGuardState.Unavailable else adRepo.fetch(adguard.baseUrl, adguard.username, adguard.password)
    }
    val closer = remember { FocusRequester() }
    PanelOverlay("Homelab", type, onDismiss) {
        if (kuma != null) {
            when (val s = kumaState) {
                is KumaState.Available -> s.snapshot.monitors
                    .sortedWith(compareBy<KumaMonitor> { if (it.status == 0) 0 else 1 }.thenBy { it.name.lowercase() })
                    .forEach { MonitorRow(it, type) }
                KumaState.Unavailable -> Text("Uptime Kuma can't be reached.", style = type.aside.copy(color = colors.warning, shadow = null))
                KumaState.Loading -> Text("Asking Uptime Kuma\u2026", style = type.aside.copy(shadow = null))
            }
        }
        if (adguard != null) {
            StarRule(Modifier.fillMaxWidth().padding(vertical = 10.dp))
            when (val s = adState) {
                is AdGuardState.Available -> {
                    val snap = s.snapshot
                    val pct = if (snap.numDnsQueries == 0L) 0 else (100.0 * snap.numBlockedFiltering / snap.numDnsQueries).toInt()
                    StatusLine(
                        ok = snap.protectionEnabled,
                        name = "AdGuard Home",
                        detail = if (snap.protectionEnabled) "${snap.numDnsQueries} queries today · $pct% blocked" else "Protection is off",
                        type = type,
                    )
                }
                AdGuardState.Unavailable -> Text("AdGuard Home can't be reached.", style = type.aside.copy(color = colors.warning, shadow = null))
                AdGuardState.Loading -> Unit
            }
        }
        PanelRow("Close", type, onDismiss, Modifier.focusRequester(closer).padding(top = 8.dp))
        FocusOnOpen(closer)
    }
}

@Composable
private fun MonitorRow(m: KumaMonitor, type: HomeType) {
    val state = when (m.status) { 1 -> "up"; 0 -> "down"; 3 -> "in maintenance"; else -> "pending" }
    val since = m.since?.let { forHowLong(it, m.sinceIsLowerBound) }
    val ping = m.pingMs?.takeIf { m.status == 1 }?.let { " · ${it.toInt()} ms" } ?: ""
    StatusLine(ok = m.status != 0, name = m.name, detail = listOfNotNull(state, since).joinToString(" ") + ping, type = type)
}

@Composable
private fun StatusLine(ok: Boolean, name: String, detail: String, type: HomeType) {
    val colors = LocalHearthColors.current
    Row(Modifier.fillMaxWidth().padding(vertical = 7.dp, horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.width(20.dp)) { Diamond(if (ok) colors.accent else colors.warning, 6.dp) }
        Text(name, style = type.body.copy(shadow = null), modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(detail, style = type.bodyMuted.copy(shadow = null, color = if (ok) colors.onSurfaceMuted else colors.warning))
    }
}

/** "for 3 h", "for 12 min", "for 2 days"; "for over …" when Kuma's history does not reach the start. */
private fun forHowLong(since: Instant, lowerBound: Boolean): String {
    val d = Duration.between(since, Instant.now()).coerceAtLeast(Duration.ZERO)
    val span = when {
        d.toDays() >= 2 -> "${d.toDays()} days"
        d.toHours() >= 1 -> "${d.toHours()} h"
        else -> "${d.toMinutes().coerceAtLeast(1)} min"
    }
    return if (lowerBound) "for over $span" else "for $span"
}
