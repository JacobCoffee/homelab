package dev.jacobcoffee.hearth.ui.home

import android.content.Context
import android.content.Intent
import android.net.Uri
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
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Text
import dev.jacobcoffee.hearth.apps.AppArt
import dev.jacobcoffee.hearth.apps.AppInfo
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors

/**
 * An app whose search can be opened with the query already filled in. Only links checked on
 * this stick are listed: Prime Video goes straight to results, Disney+ after its profile pick.
 */
private data class SearchLink(val label: String, val packageName: String, val url: (String) -> String)

private val SEARCH_LINKS = listOf(
    SearchLink("Prime Video", "com.amazon.firebat") { "https://app.primevideo.com/search?phrase=${Uri.encode(it)}" },
    SearchLink("Disney+", "com.disney.disneyplus") { "https://www.disneyplus.com/search?q=${Uri.encode(it)}" },
)

private const val MAX_APP_MATCHES = 4

/**
 * Typed search for a remote with only arrows, OK and Back: the Fire OS keyboard fills the field,
 * then the results offer the apps' own searches and any installed app whose name matches.
 */
@Composable
fun SearchPanel(apps: List<AppInfo>, type: HomeType, onLaunchApp: (AppInfo) -> Unit) {
    val colors = LocalHearthColors.current
    val context = LocalContext.current
    val focusManager = LocalFocusManager.current
    val field = remember { FocusRequester() }
    var query by remember { mutableStateOf("") }
    val installed = remember(apps) { apps.associateBy { it.packageName } }
    val q = query.trim()
    val links = if (q.isEmpty()) emptyList() else SEARCH_LINKS.filter { it.packageName in installed }
    val matches = if (q.isEmpty()) emptyList() else apps.filter { it.label.contains(q, ignoreCase = true) }.take(MAX_APP_MATCHES)

    LaunchedEffect(Unit) { runCatching { field.requestFocus() } }

    Box(Modifier.fillMaxSize().background(colors.background.copy(alpha = 0.94f))) {
        Column(Modifier.fillMaxSize().padding(horizontal = 120.dp, vertical = 60.dp)) {
            Text(displayText("Search"), style = type.greeting)
            StarRule(Modifier.fillMaxWidth().padding(vertical = 18.dp))
            SearchField(
                value = query,
                onValueChange = { query = it },
                type = type,
                modifier = Modifier.focusRequester(field),
                onSubmit = { focusManager.moveFocus(FocusDirection.Down) },
            )
            Spacer(Modifier.height(26.dp))
            if (q.isEmpty()) {
                Text(
                    "Press OK to type with the on-screen keyboard. Prime Video and Disney+ open straight to their results.",
                    style = type.aside,
                )
            } else if (links.isEmpty() && matches.isEmpty()) {
                Text("No app is named \u201c$q\u201d, and no installed app can search for it.", style = type.aside)
            }
            links.forEach { link ->
                ResultRow(
                    label = "Search ${link.label} for \u201c$q\u201d",
                    app = installed.getValue(link.packageName),
                    type = type,
                    onClick = { openSearch(context, link, q) },
                )
            }
            matches.forEach { app ->
                ResultRow(label = "Open ${app.label}", app = app, type = type, onClick = { onLaunchApp(app) })
            }
        }
    }
}

private fun openSearch(context: Context, link: SearchLink, query: String) {
    val intent = Intent(Intent.ACTION_VIEW, Uri.parse(link.url(query)))
        .setPackage(link.packageName)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    runCatching { context.startActivity(intent) }
}

/** A dark well with a 1px edge that turns to the accent while focused, like the guild's fields. */
@Composable
private fun SearchField(value: String, onValueChange: (String) -> Unit, type: HomeType, modifier: Modifier, onSubmit: () -> Unit) {
    val colors = LocalHearthColors.current
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    BasicTextField(
        value = value,
        onValueChange = onValueChange,
        singleLine = true,
        interactionSource = interaction,
        textStyle = type.body.copy(fontSize = type.greeting.fontSize * 0.8f, shadow = null),
        cursorBrush = SolidColor(colors.accent),
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
        keyboardActions = KeyboardActions(onSearch = { onSubmit() }, onDone = { onSubmit() }),
        modifier = modifier.fillMaxWidth(),
        decorationBox = { inner ->
            Row(
                Modifier
                    .fillMaxWidth()
                    .background(colors.background.copy(alpha = 0.62f))
                    .drawBehind {
                        val s = 1.dp.toPx()
                        drawRect(if (focused) colors.accent else colors.line, topLeft = Offset(s / 2, s / 2), size = size.copy(size.width - s, size.height - s), style = Stroke(s))
                    }
                    .padding(horizontal = 18.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                MagnifierGlyph()
                Spacer(Modifier.width(14.dp))
                Box(Modifier.weight(1f)) {
                    if (value.isEmpty()) Text("Title, show or app", style = type.aside)
                    inner()
                }
            }
        },
    )
}

@Composable
private fun MagnifierGlyph() {
    val color = LocalHearthColors.current.onSurfaceMuted
    Canvas(Modifier.size(20.dp)) {
        val u = size.minDimension / 20f
        val stroke = 1.6f * u
        drawCircle(color, 6.5f * u, Offset(8.5f * u, 8.5f * u), style = Stroke(stroke))
        drawLine(color, Offset(13.3f * u, 13.3f * u), Offset(18f * u, 18f * u), stroke, StrokeCap.Round)
    }
}

@Composable
private fun ResultRow(label: String, app: AppInfo, type: HomeType, onClick: () -> Unit) {
    val colors = LocalHearthColors.current
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    val context = LocalContext.current
    val icon = remember(app) { AppArt.icon(context, app) }
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(interactionSource = interaction, indication = null, onClick = onClick)
            .drawBehind {
                if (focused) drawRect(colors.accent.copy(alpha = 0.10f))
                val y = size.height - 0.5.dp.toPx()
                drawLine(colors.line, Offset(0f, y), Offset(size.width, y), 1.dp.toPx())
            }
            .padding(vertical = 12.dp, horizontal = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.width(20.dp)) { if (focused) Diamond(colors.accent, 6.dp) }
        Image(icon, contentDescription = null, modifier = Modifier.size(30.dp))
        Spacer(Modifier.width(14.dp))
        Text(label, style = type.body.copy(color = if (focused) colors.accent else colors.onSurface, shadow = null))
    }
}
