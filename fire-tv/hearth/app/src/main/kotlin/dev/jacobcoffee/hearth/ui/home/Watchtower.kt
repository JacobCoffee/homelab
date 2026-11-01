package dev.jacobcoffee.hearth.ui.home

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Text
import dev.jacobcoffee.hearth.config.AdGuardConfig
import dev.jacobcoffee.hearth.config.HearthConfig
import dev.jacobcoffee.hearth.config.UptimeKumaConfig
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors
import dev.jacobcoffee.hearth.widgets.AdGuardRepository
import dev.jacobcoffee.hearth.widgets.AdGuardState
import dev.jacobcoffee.hearth.widgets.KumaState
import dev.jacobcoffee.hearth.widgets.UptimeKumaRepository

/** Homelab health in one line: shown only for services that are configured. */
@Composable
fun Watchtower(config: HearthConfig, type: HomeType) {
    val colors = LocalHearthColors.current
    Row(verticalAlignment = Alignment.CenterVertically) {
        config.uptimeKuma?.let { KumaStatus(it, type) }
        if (config.uptimeKuma != null && config.adguard != null) {
            Diamond(colors.ornamentDim, 4.dp, modifier = Modifier.padding(horizontal = 14.dp))
        }
        config.adguard?.let { AdGuardStatus(it, type) }
    }
}

@Composable
private fun StatusMark(ok: Boolean) {
    val colors = LocalHearthColors.current
    Diamond(if (ok) colors.accent else colors.warning, 6.dp)
    Spacer(Modifier.width(8.dp))
}

@Composable
private fun KumaStatus(config: UptimeKumaConfig, type: HomeType) {
    val colors = LocalHearthColors.current
    val repo = remember { UptimeKumaRepository() }
    val state by rememberPolled(config, KumaState.Loading as KumaState, 60_000L) { repo.fetch(config.baseUrl, config.statusPageSlug) }
    when (val s = state) {
        is KumaState.Available -> Row(verticalAlignment = Alignment.CenterVertically) {
            val snap = s.snapshot
            val total = snap.upCount + snap.downCount
            StatusMark(ok = snap.downCount == 0)
            Text("UPTIME  ${snap.upCount} OF $total", style = type.label)
            if (snap.downNames.isNotEmpty()) {
                val extra = if (snap.downNames.size > 1) " +${snap.downNames.size - 1}" else ""
                Text(
                    "   ${snap.downNames.first()}$extra DOWN".uppercase(),
                    style = type.label.copy(color = colors.warning),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.widthIn(max = 220.dp),
                )
            }
        }
        KumaState.Unavailable -> Text("UPTIME UNREACHABLE", style = type.label.copy(color = colors.warning))
        KumaState.Loading -> Unit
    }
}

@Composable
private fun AdGuardStatus(config: AdGuardConfig, type: HomeType) {
    val colors = LocalHearthColors.current
    val repo = remember { AdGuardRepository() }
    val state by rememberPolled(config, AdGuardState.Loading as AdGuardState, 60_000L) {
        repo.fetch(config.baseUrl, config.username, config.password)
    }
    when (val s = state) {
        is AdGuardState.Available -> Row(verticalAlignment = Alignment.CenterVertically) {
            val snap = s.snapshot
            StatusMark(ok = snap.protectionEnabled)
            val text = when {
                !snap.protectionEnabled -> "ADGUARD OFF"
                snap.numDnsQueries == 0L -> "ADGUARD ON"
                else -> "ADGUARD  ${(100.0 * snap.numBlockedFiltering / snap.numDnsQueries).toInt()}% OF ${compact(snap.numDnsQueries)} BLOCKED"
            }
            Text(text, style = type.label.copy(color = if (snap.protectionEnabled) colors.onSurfaceMuted else colors.warning))
        }
        AdGuardState.Unavailable -> Text("ADGUARD UNREACHABLE", style = type.label.copy(color = colors.warning))
        AdGuardState.Loading -> Unit
    }
}

private fun compact(n: Long): String = when {
    n >= 1_000_000 -> "%.1fM".format(n / 1_000_000.0)
    n >= 10_000 -> "${n / 1000}K"
    n >= 1_000 -> "%.1fK".format(n / 1000.0)
    else -> n.toString()
}
