package dev.jacobcoffee.hearth.ui

import android.app.Activity
import android.content.Intent
import androidx.activity.compose.BackHandler
import android.net.Uri
import android.provider.Settings
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.animateOffsetAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
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
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.tv.material3.Text
import dev.jacobcoffee.hearth.apps.AppInfo
import dev.jacobcoffee.hearth.apps.FireTvSettings
import dev.jacobcoffee.hearth.homekey.HomeRedirectService
import dev.jacobcoffee.hearth.homekey.CoverSnapshot
import dev.jacobcoffee.hearth.theme.ParticleField
import dev.jacobcoffee.hearth.ui.home.Diamond
import dev.jacobcoffee.hearth.ui.home.AppsPanel
import dev.jacobcoffee.hearth.ui.home.CommandRail
import dev.jacobcoffee.hearth.ui.home.HomelabPanel
import dev.jacobcoffee.hearth.ui.home.InputsPanel
import dev.jacobcoffee.hearth.ui.home.PowerPanel
import dev.jacobcoffee.hearth.ui.home.RAIL_WIDTH
import dev.jacobcoffee.hearth.ui.home.RailItem
import dev.jacobcoffee.hearth.ui.home.Sigil
import dev.jacobcoffee.hearth.ui.home.HomeHeader
import dev.jacobcoffee.hearth.ui.home.HomeType
import dev.jacobcoffee.hearth.ui.home.QuestLog
import dev.jacobcoffee.hearth.ui.home.SearchPanel
import dev.jacobcoffee.hearth.ui.home.StarRule
import dev.jacobcoffee.hearth.ui.home.displayText
import dev.jacobcoffee.hearth.ui.home.rememberHomeType
import dev.jacobcoffee.hearth.media.NowPlayingSource
import dev.jacobcoffee.hearth.ui.home.NowPlayingStrip
import dev.jacobcoffee.hearth.ui.home.STRIP_HEIGHT
import dev.jacobcoffee.hearth.ui.home.StripDivider
import dev.jacobcoffee.hearth.ui.home.nightScrim
import dev.jacobcoffee.hearth.ui.home.rememberNowPlaying
import dev.jacobcoffee.hearth.widgets.TvLink
import androidx.compose.runtime.rememberCoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors
import dev.jacobcoffee.hearth.ui.theme.LocalHearthTheme

/** TV overscan safe area: 5% of a 960x540dp screen. */
private val SAFE_X = 48.dp
private val SAFE_Y = 27.dp

private const val AMAZON_LAUNCHER = "com.amazon.tv.launcher"

/** What a tile's long-press menu acts on: a grid app can also be moved, a footer link cannot. */
private data class MenuTarget(val app: AppInfo, val movable: Boolean)

/** What the rail has open over the home screen. */
private enum class Panel { None, Search, Apps, Inputs, Power, Homelab }

@Composable
fun LauncherScreen(deps: HearthDependencies, onOpenSettings: (themesOnly: Boolean) -> Unit) {
    val context = LocalContext.current
    val theme = LocalHearthTheme.current.json
    val type = rememberHomeType()
    val scope = rememberCoroutineScope()

    var config by remember { mutableStateOf(deps.config.load()) }
    var apps by remember { mutableStateOf(deps.apps.visibleAppsInOrder()) }
    var others by remember { mutableStateOf(deps.apps.systemLauncherApps()) }
    var menuFor by remember { mutableStateOf<MenuTarget?>(null) }
    var settingsMenu by remember { mutableStateOf(false) }
    var light by remember { mutableStateOf<Pair<String, Rect>?>(null) }
    var arrivals by remember { mutableIntStateOf(0) }
    // The TCL remote never delivers Home, Menu or a long press, so every option those would
    // reach is on the rail: Arrange turns OK on a tile into its menu, Search types a query.
    var arranging by remember { mutableStateOf(false) }
    var panel by remember { mutableStateOf(Panel.None) }
    var notice by remember { mutableStateOf<String?>(null) }
    var railOpen by remember { mutableStateOf(false) }
    val firstTile = remember { FocusRequester() }
    val nowPlayingFocus = remember { FocusRequester() }
    val nowPlaying = remember { NowPlayingSource(context) }
    val playing = rememberNowPlaying(nowPlaying)

    fun closePanel() { panel = Panel.None; arrivals++ }
    BackHandler(enabled = panel == Panel.Search) { closePanel() }
    BackHandler(enabled = arranging && panel == Panel.None) { arranging = false }
    LaunchedEffect(notice) { if (notice != null) { delay(6000); notice = null } }

    // Each return to Home: pick up installs and pushed config, apply a pushed layout, and land
    // focus on the first app.
    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(lifecycleOwner) {
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            config = deps.config.load()
            deps.apps.applyLayout(config.apps)
            apps = deps.apps.visibleAppsInOrder()
            others = deps.apps.systemLauncherApps()
            arrivals++
        }
    }
    LaunchedEffect(arrivals, apps.firstOrNull()?.packageName) {
        if (apps.isNotEmpty() && panel == Panel.None) runCatching { firstTile.requestFocus() }
    }
    // Once Home has settled, picture it for the redirect's cover (see CoverSnapshot).
    LaunchedEffect(arrivals, theme.id, theme.version) {
        val activity = context as? Activity ?: return@LaunchedEffect
        delay(1500)
        if (panel == Panel.None && !railOpen) CoverSnapshot.capture(activity)
    }

    fun launch(app: AppInfo) {
        val intent = context.packageManager.getLaunchIntentForPackage(app.packageName)
            ?: Intent().setComponent(app.component)
        intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK
        if (app.packageName == AMAZON_LAUNCHER) HomeRedirectService.allowAmazonHomeUntilHearthReturns()
        context.startActivity(intent)
        deps.apps.recordLaunch(app.packageName)
    }

    // Amazon's own launcher calls itself "Home", which reads as this screen.
    val systemApps = others.map { if (it.packageName == AMAZON_LAUNCHER) it.copy(label = "Amazon Home") else it }
    val tv = config.tv
    val rail = buildList {
        add(RailItem(Sigil.Search, "Search") { panel = Panel.Search })
        add(RailItem(Sigil.Arrange, if (arranging) "Done arranging" else "Arrange") { arranging = !arranging })
        add(RailItem(Sigil.Apps, "Apps") { panel = Panel.Apps })
        if (tv != null) {
            add(RailItem(Sigil.Inputs, "Inputs") { panel = Panel.Inputs })
            add(RailItem(Sigil.TvSettings, "TV settings") {
                scope.launch {
                    if (!TvLink.send(tv, "tvsettings")) notice = "The TV didn't answer. Run tcl-tv/bridge/bridge.sh start on the Mac."
                }
            })
            add(RailItem(Sigil.Power, "Power") { panel = Panel.Power })
        }
        // Fire OS gates its controllers deep links (CONTROLLERS_SETTINGS, DISCOVER_INPUT_DEVICE)
        // behind the parental-controls PIN for outside callers and then opens nothing, so this
        // goes through the settings grid, where Controllers & Bluetooth Devices is one press away.
        add(RailItem(Sigil.Controllers, "Controllers") { FireTvSettings.open(context) })
        if (config.uptimeKuma != null || config.adguard != null) add(RailItem(Sigil.Homelab, "Homelab") { panel = Panel.Homelab })
        add(RailItem(Sigil.Theme, "Theme") { onOpenSettings(true) })
        add(RailItem(Sigil.Settings, "Settings") { settingsMenu = true })
    }

    Box(Modifier.fillMaxSize()) {
        ParticleField(theme.effect, Modifier.fillMaxSize())
        Torchlight(light?.second)
        Column(Modifier.fillMaxSize().padding(start = RAIL_WIDTH + 18.dp, end = SAFE_X, top = SAFE_Y, bottom = SAFE_Y)) {
            HomeHeader(config, type)
            Spacer(Modifier.height(12.dp))
            val guild = config.guild
            if (guild != null || playing != null) {
                StarRule(Modifier.fillMaxWidth())
                // Music shares the strip with the guild's next quest rather than pushing the grid down.
                Row(Modifier.fillMaxWidth().height(STRIP_HEIGHT).nightScrim(), verticalAlignment = Alignment.CenterVertically) {
                    guild?.let { QuestLog(it, type, maxShown = if (playing != null) 1 else 2, modifier = Modifier.weight(1f)) }
                    if (guild != null && playing != null) StripDivider()
                    playing?.let { NowPlayingStrip(it, nowPlaying, type, Modifier.weight(1f), focusRequester = nowPlayingFocus) }
                }
            }
            Spacer(Modifier.height(14.dp))
            val banner = notice ?: if (arranging) "ARRANGING  \u00b7  CHOOSE AN APP TO MOVE OR HIDE  \u00b7  BACK WHEN DONE" else null
            if (banner != null) {
                Text(
                    banner.uppercase(),
                    style = type.label.copy(color = if (notice != null) LocalHearthColors.current.warning else LocalHearthColors.current.accent),
                    modifier = Modifier.align(Alignment.CenterHorizontally).padding(bottom = 6.dp),
                )
            }
            AppGrid(
                apps = apps,
                type = type,
                firstTileFocus = firstTile,
                // Up from the top row goes to the music; without this the left tiles, which sit
                // under the guild's quest (not focusable), go to the rail instead.
                upFromFirstRow = if (playing != null) nowPlayingFocus else null,
                onLaunch = { if (arranging) menuFor = MenuTarget(it, movable = true) else launch(it) },
                onMenu = { menuFor = MenuTarget(it, movable = true) },
                onFocusedBounds = { pkg, bounds ->
                    if (bounds != null) light = pkg to bounds
                    else if (light?.first == pkg) light = null
                },
                modifier = Modifier.fillMaxWidth().weight(1f),
            )
        }
        CommandRail(rail, type, Modifier.align(Alignment.CenterStart), onOpenChanged = { railOpen = it })
        when (panel) {
            Panel.None -> Unit
            Panel.Search -> SearchPanel(apps + systemApps, type, onLaunchApp = ::launch)
            Panel.Apps -> AppsPanel(systemApps, type, onLaunch = {
                if (arranging) menuFor = MenuTarget(it, movable = false) else { closePanel(); launch(it) }
            }, onDismiss = ::closePanel)
            Panel.Inputs -> tv?.let { InputsPanel(it, type, onDismiss = ::closePanel) }
            Panel.Power -> tv?.let { PowerPanel(it, type, onDismiss = ::closePanel) }
            Panel.Homelab -> HomelabPanel(config.uptimeKuma, config.adguard, type, onDismiss = ::closePanel)
        }
    }

    if (settingsMenu) {
        TileMenu(
            title = "Settings",
            type = type,
            onDismiss = { settingsMenu = false },
            actions = listOf(
                "Fire TV settings" to {
                    settingsMenu = false
                    FireTvSettings.open(context)
                },
                "Hearth settings" to { settingsMenu = false; onOpenSettings(false) },
            ),
        )
    }

    menuFor?.let { target ->
        val app = target.app
        val index = apps.indexOf(app)
        TileMenu(
            title = app.label,
            type = type,
            onDismiss = { menuFor = null },
            actions = buildList {
                if (target.movable && index > 0) add("Move earlier" to { deps.apps.moveApp(app.packageName, index - 1) })
                if (target.movable && index in 0 until apps.size - 1) add("Move later" to { deps.apps.moveApp(app.packageName, index + 1) })
                add("Hide from Home" to { deps.apps.setHidden(app.packageName, true) })
                add("App info" to {
                    context.startActivity(
                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                            .setData(Uri.fromParts("package", app.packageName, null))
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                })
            }.map { (label, action) ->
                label to {
                    action()
                    apps = deps.apps.visibleAppsInOrder()
                    others = deps.apps.systemLauncherApps()
                    menuFor = null
                }
            },
        )
    }
}

/**
 * A warm pool of light on the background under the focused tile, like a torch held over it.
 * It glides between tiles and fades out when focus leaves the grid. Themes without a
 * focusLight color draw nothing.
 */
@Composable
private fun Torchlight(target: Rect?) {
    val colors = LocalHearthColors.current
    val glow = colors.focusLight ?: return
    // Remembers where the light last stood so it can fade out in place, not jump to a corner.
    val lastAnchor = remember { arrayOfNulls<Rect>(1) }
    if (target != null) lastAnchor[0] = target
    val anchor = lastAnchor[0] ?: return
    // The pool sits low, below the tile's center, as light falling on the ground in front of it.
    val target0 = anchor.center + Offset(0f, anchor.height * 0.45f)
    val center by animateOffsetAsState(target0, tween(520, easing = EaseOutExpo), label = "torch")
    val strength by animateFloatAsState(if (target != null) 1f else 0f, tween(420, easing = EaseOutExpo), label = "torchStrength")
    val radius = anchor.width * 1.05f
    Canvas(Modifier.fillMaxSize()) {
        if (strength <= 0.01f) return@Canvas
        // Wider than tall, and screened onto the painting so it brightens rather than tints.
        scale(scaleX = 1.6f, scaleY = 1f, pivot = center) {
            drawCircle(
                Brush.radialGradient(
                    0f to glow.copy(alpha = 0.58f * strength),
                    0.38f to glow.copy(alpha = 0.26f * strength),
                    0.7f to glow.copy(alpha = 0.07f * strength),
                    1f to Color.Transparent,
                    center = center,
                    radius = radius,
                ),
                radius = radius,
                center = center,
                blendMode = BlendMode.Screen,
            )
        }
    }
}

/** A long-press menu drawn as one of the guild's chambers: a framed night panel. */
@Composable
private fun TileMenu(title: String, type: HomeType, actions: List<Pair<String, () -> Unit>>, onDismiss: () -> Unit) {
    val colors = LocalHearthColors.current
    val first = remember { FocusRequester() }
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier
                .width(340.dp)
                .background(colors.background.copy(alpha = 0.97f))
                .drawBehind {
                    val s = 1.dp.toPx()
                    drawRect(colors.line, topLeft = Offset(s / 2, s / 2), size = size.copy(size.width - s, size.height - s), style = Stroke(s))
                    val i = 5.dp.toPx()
                    drawRect(colors.ornamentDim, topLeft = Offset(i, i), size = size.copy(size.width - i * 2, size.height - i * 2), style = Stroke(s))
                }
                .padding(horizontal = 30.dp, vertical = 26.dp),
        ) {
            Text(displayText(title), style = type.questTitle.copy(fontSize = 18.sp, shadow = null), maxLines = 1)
            StarRule(Modifier.fillMaxWidth().padding(vertical = 12.dp))
            actions.forEachIndexed { i, (label, onClick) ->
                MenuAction(label, type, onClick, if (i == 0) Modifier.focusRequester(first) else Modifier)
            }
        }
        LaunchedEffect(Unit) { runCatching { first.requestFocus() } }
    }
}

@Composable
private fun MenuAction(label: String, type: HomeType, onClick: () -> Unit, modifier: Modifier) {
    val colors = LocalHearthColors.current
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    Row(
        modifier
            .fillMaxWidth()
            .clickable(interactionSource = interaction, indication = null, onClick = onClick)
            .padding(vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.width(18.dp)) {
            if (focused) Diamond(colors.accent, 6.dp)
        }
        Text(label, style = type.body.copy(color = if (focused) colors.accent else colors.onSurface, shadow = null))
    }
}
