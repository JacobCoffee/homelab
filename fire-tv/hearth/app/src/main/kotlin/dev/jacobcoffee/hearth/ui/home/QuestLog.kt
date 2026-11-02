package dev.jacobcoffee.hearth.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Text
import dev.jacobcoffee.hearth.config.GuildConfig
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors
import dev.jacobcoffee.hearth.widgets.GuildEvent
import dev.jacobcoffee.hearth.widgets.GuildEventsRepository
import dev.jacobcoffee.hearth.widgets.GuildEventsState
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit

/** The strip under the header. Height is fixed so loading, empty and error states never move the app grid. */
val STRIP_HEIGHT = 58.dp

/**
 * The guild's next quests in the home strip: a gold quest mark, the title in display type,
 * kind and place beneath, and when it starts at the right. [maxShown] drops to one when
 * Now Playing shares the strip.
 */
@Composable
fun QuestLog(config: GuildConfig, type: HomeType, maxShown: Int, modifier: Modifier = Modifier) {
    val repo = remember { GuildEventsRepository() }
    val state by rememberPolled(config, GuildEventsState.Loading as GuildEventsState, 15 * 60 * 1000L) {
        repo.fetch(config.baseUrl, config.maxEvents)
    }
    Box(modifier.height(STRIP_HEIGHT), contentAlignment = Alignment.CenterStart) {
        when (val s = state) {
            is GuildEventsState.Available -> if (s.events.isEmpty()) {
                Text("No quests posted yet.", style = type.aside)
            } else {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    s.events.take(maxShown).forEachIndexed { i, event ->
                        if (i > 0) StripDivider()
                        QuestEntry(event, type, Modifier.weight(1f))
                    }
                }
            }
            GuildEventsState.Unavailable -> Text("The notice board can't be reached right now.", style = type.aside)
            GuildEventsState.Loading -> Unit
        }
    }
}

/** A short vertical hairline between entries in the home strip. */
@Composable
fun StripDivider() {
    val colors = LocalHearthColors.current
    Box(
        Modifier
            .padding(horizontal = 28.dp)
            .width(1.dp)
            .height(30.dp)
            .background(colors.line),
    )
}

@Composable
private fun QuestEntry(event: GuildEvent, type: HomeType, modifier: Modifier) {
    val colors = LocalHearthColors.current
    val local = event.startsAt.withZoneSameInstant(ZoneId.systemDefault())
    val meta = listOfNotNull(event.kind.name, event.location?.takeIf { it.isNotBlank() }).joinToString("  ·  ").uppercase()
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        QuestMark(Modifier.padding(end = 14.dp))
        Column(Modifier.weight(1f)) {
            Text(displayText(event.title), style = type.questTitle, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(5.dp))
            Text(meta, style = type.label, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.width(20.dp))
        Column(horizontalAlignment = Alignment.End) {
            Text(displayText(local.format(DateTimeFormatter.ofPattern("EEE  h:mm a"))), style = type.questWhen)
            Spacer(Modifier.height(5.dp))
            Text(relativeDay(local.toLocalDate(), local.hour), style = type.label.copy(color = colors.accent))
        }
    }
}

private fun relativeDay(date: LocalDate, hour: Int): String {
    val days = ChronoUnit.DAYS.between(LocalDate.now(), date)
    return when {
        days < 0 -> "UNDER WAY"
        days == 0L -> if (hour >= 17) "TONIGHT" else "TODAY"
        days == 1L -> if (hour >= 17) "TOMORROW NIGHT" else "TOMORROW"
        days < 7 -> "IN $days DAYS"
        else -> date.format(DateTimeFormatter.ofPattern("MMM d")).uppercase()
    }
}
