package dev.jacobcoffee.hearth.ui

import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Text
import dev.jacobcoffee.hearth.BuildConfig
import dev.jacobcoffee.hearth.catalog.CatalogFetchResult
import dev.jacobcoffee.hearth.catalog.CatalogThemeEntry
import dev.jacobcoffee.hearth.catalog.DownloadResult
import dev.jacobcoffee.hearth.theme.ResolvedTheme
import dev.jacobcoffee.hearth.theme.ThemeRepository
import dev.jacobcoffee.hearth.theme.ThemeResolutionReason
import dev.jacobcoffee.hearth.ui.theme.HearthTheme
import dev.jacobcoffee.hearth.ui.theme.LocalHearthColors
import kotlinx.coroutines.launch

private sealed class SettingsSection {
    object Menu : SettingsSection()
    object ThemePicker : SettingsSection()
    object ThemeStore : SettingsSection()
    object HiddenApps : SettingsSection()
    object About : SettingsSection()
}

@Composable
fun SettingsScreen(
    deps: HearthDependencies,
    themeRepository: ThemeRepository,
    reduceMotion: Boolean,
    onReduceMotionChanged: (Boolean) -> Unit,
    onThemeChanged: () -> Unit,
    onClose: () -> Unit,
    /** Opened from the Theme sigil: start at the picker, and Back leaves Settings entirely. */
    themesOnly: Boolean = false,
) {
    val home = if (themesOnly) SettingsSection.ThemePicker else SettingsSection.Menu
    var section by remember { mutableStateOf(home) }
    BackHandler {
        if (section == home) onClose() else section = home
    }

    val colors = LocalHearthColors.current
    Box(modifier = Modifier.fillMaxSize()) {
        when (val s = section) {
            SettingsSection.Menu -> SettingsMenu(
                reduceMotion = reduceMotion,
                hasCatalog = deps.config.load().themeCatalogUrl != null,
                onSelect = { section = it },
                onReduceMotionChanged = onReduceMotionChanged,
            )
            SettingsSection.ThemePicker -> ThemePickerScreen(
                themeRepository = themeRepository,
                onThemeChanged = onThemeChanged,
                onClose = { if (themesOnly) onClose() else section = SettingsSection.Menu },
            )
            SettingsSection.ThemeStore -> ThemeStoreScreen(
                deps = deps,
                themeRepository = themeRepository,
                onClose = { section = SettingsSection.Menu },
            )
            SettingsSection.HiddenApps -> HiddenAppsScreen(deps = deps, onClose = { section = SettingsSection.Menu })
            SettingsSection.About -> AboutScreen(deps = deps, themeRepository = themeRepository, onClose = { section = SettingsSection.Menu })
        }
    }
}

@Composable
private fun SettingsMenu(
    reduceMotion: Boolean,
    hasCatalog: Boolean,
    onSelect: (SettingsSection) -> Unit,
    onReduceMotionChanged: (Boolean) -> Unit,
) {
    val colors = LocalHearthColors.current
    Row(modifier = Modifier.fillMaxSize().background(colors.background.copy(alpha = 0.92f))) {
        Column(
            modifier = Modifier
                .width(360.dp)
                .fillMaxHeight()
                .padding(32.dp),
        ) {
            Text("Settings", color = colors.onSurface, fontSize = 22.sp, fontWeight = FontWeight.Bold)
            SettingsMenuItem("Theme", colors) { onSelect(SettingsSection.ThemePicker) }
            if (hasCatalog) SettingsMenuItem("Theme store", colors) { onSelect(SettingsSection.ThemeStore) }
            SettingsMenuItem(if (reduceMotion) "Reduce motion: On" else "Reduce motion: Off", colors) {
                onReduceMotionChanged(!reduceMotion)
            }
            SettingsMenuItem("Hidden apps", colors) { onSelect(SettingsSection.HiddenApps) }
            SettingsMenuItem("About", colors) { onSelect(SettingsSection.About) }
        }
    }
}

@Composable
private fun SettingsMenuItem(label: String, colors: dev.jacobcoffee.hearth.ui.theme.HearthColors, onClick: () -> Unit) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 16.dp)
            .background(colors.surface, RoundedCornerShape(8.dp))
            .clickableFocusable(onClick = onClick),
    ) {
        Text(label, color = colors.onSurface, fontSize = 16.sp, modifier = Modifier.padding(16.dp))
    }
}

/** Theme list with a live preview: the background behind the menu is the hovered theme's. */
@Composable
private fun ThemePickerScreen(themeRepository: ThemeRepository, onThemeChanged: () -> Unit, onClose: () -> Unit) {
    val allThemes = remember { themeRepository.loadAll() }
    val selected = remember { mutableStateOf(themeRepository.selectedThemeId()) }
    var previewId by remember { mutableStateOf(selected.value ?: "default") }
    val previewTheme = allThemes[previewId] ?: allThemes.values.first()

    // A still preview: the screen behind Settings already owns the ambient clock.
    HearthTheme(previewTheme, reduceMotion = true) {
        val colors = LocalHearthColors.current
        Row(modifier = Modifier.fillMaxSize()) {
            // Scrolls so every theme is reachable: focus moving past the last visible row
            // brings that row into view.
            Column(
                modifier = Modifier
                    .width(360.dp)
                    .fillMaxHeight()
                    .background(colors.background.copy(alpha = 0.92f))
                    .verticalScroll(rememberScrollState())
                    .padding(32.dp),
            ) {
                Text("Theme", color = colors.onSurface, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                ThemeRow("Automatic", selected.value == null, colors) {
                    themeRepository.setSelectedThemeId(null)
                    selected.value = null
                    onThemeChanged()
                }
                allThemes.values.sortedBy { it.json.name }.forEach { rt ->
                    ThemeRow(
                        label = rt.json.name,
                        active = selected.value == rt.json.id,
                        colors = colors,
                        onFocus = { previewId = rt.json.id },
                        onClick = {
                            themeRepository.setSelectedThemeId(rt.json.id)
                            selected.value = rt.json.id
                            onThemeChanged()
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun ThemeRow(
    label: String,
    active: Boolean,
    colors: dev.jacobcoffee.hearth.ui.theme.HearthColors,
    onFocus: (() -> Unit)? = null,
    onClick: () -> Unit,
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 12.dp)
            .background(if (active) colors.surfaceFocused else colors.surface, RoundedCornerShape(8.dp))
            .then(
                if (onFocus != null) {
                    Modifier.onFocusEnter(onFocus)
                } else Modifier
            )
            .clickableFocusable(onClick = onClick),
    ) {
        Text(
            (if (active) "\u2713 " else "") + label,
            color = colors.onSurface,
            fontSize = 15.sp,
            modifier = Modifier.padding(14.dp),
        )
    }
}

@Composable
private fun ThemeStoreScreen(deps: HearthDependencies, themeRepository: ThemeRepository, onClose: () -> Unit) {
    val colors = LocalHearthColors.current
    val config = remember { deps.config.load() }
    val catalogUrl = config.themeCatalogUrl
    var entries by remember { mutableStateOf<List<CatalogThemeEntry>>(emptyList()) }
    var baseUrl by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("Loading catalog…") }
    var downloaded by remember { mutableStateOf(deps.catalog.downloadedThemeIds()) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(catalogUrl) {
        if (catalogUrl == null) {
            status = "No theme catalog configured"
            return@LaunchedEffect
        }
        when (val result = deps.catalog.fetchIndex(catalogUrl)) {
            is CatalogFetchResult.Success -> {
                entries = result.index.themes
                baseUrl = result.baseUrl
                status = if (entries.isEmpty()) "Catalog is empty" else ""
            }
            is CatalogFetchResult.Failure -> status = "Catalog unavailable: ${result.reason}"
        }
    }

    Column(modifier = Modifier.fillMaxSize().background(colors.background.copy(alpha = 0.92f)).padding(32.dp)) {
        Text("Theme store", color = colors.onSurface, fontSize = 22.sp, fontWeight = FontWeight.Bold)
        if (status.isNotEmpty()) Text(status, color = colors.onSurfaceMuted, fontSize = 14.sp, modifier = Modifier.padding(top = 12.dp))
        LazyColumn(modifier = Modifier.padding(top = 16.dp)) {
            items(entries, key = { it.id }) { entry ->
                var rowStatus by remember(entry.id) { mutableStateOf("") }
                val installed = entry.id in downloaded
                Row(
                    modifier = Modifier.fillMaxWidth().padding(bottom = 10.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column {
                        Text(entry.name, color = colors.onSurface, fontSize = 15.sp)
                        Text(
                            if (rowStatus.isNotEmpty()) rowStatus else if (installed) "Installed" else "v${entry.version} · ${entry.size / 1024} KB",
                            color = colors.onSurfaceMuted, fontSize = 12.sp,
                        )
                    }
                    Row {
                        Box(
                            modifier = Modifier
                                .background(colors.surface, RoundedCornerShape(6.dp))
                                .clickableFocusable(onClick = {
                                    rowStatus = "Downloading…"
                                    scope.launch {
                                        when (val result = deps.catalog.download(entry, baseUrl)) {
                                            is DownloadResult.Success -> {
                                                rowStatus = "Installed v${result.version}"
                                                downloaded = deps.catalog.downloadedThemeIds()
                                            }
                                            is DownloadResult.Failure -> rowStatus = "Failed: ${result.reason}"
                                        }
                                    }
                                }),
                        ) {
                            Text(if (installed) "Update" else "Download", color = colors.onSurface, fontSize = 13.sp, modifier = Modifier.padding(10.dp))
                        }
                        if (installed) {
                            Box(
                                modifier = Modifier
                                    .padding(start = 8.dp)
                                    .background(colors.surface, RoundedCornerShape(6.dp))
                                    .clickableFocusable(onClick = {
                                        deps.catalog.delete(entry.id)
                                        downloaded = deps.catalog.downloadedThemeIds()
                                        rowStatus = "Deleted"
                                    }),
                            ) {
                                Text("Delete", color = colors.accent, fontSize = 13.sp, modifier = Modifier.padding(10.dp))
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun HiddenAppsScreen(deps: HearthDependencies, onClose: () -> Unit) {
    val colors = LocalHearthColors.current
    var apps by remember { mutableStateOf(deps.apps.homeApps() + deps.apps.systemLauncherApps(includeHidden = true)) }
    var hidden by remember { mutableStateOf(deps.apps.hiddenPackages()) }
    Column(modifier = Modifier.fillMaxSize().background(colors.background.copy(alpha = 0.92f)).padding(32.dp)) {
        Text("Hidden apps", color = colors.onSurface, fontSize = 22.sp, fontWeight = FontWeight.Bold)
        LazyColumn(modifier = Modifier.padding(top = 16.dp)) {
            items(apps, key = { it.packageName }) { app ->
                val isHidden = app.packageName in hidden
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 8.dp)
                        .background(colors.surface, RoundedCornerShape(8.dp))
                        .clickableFocusable(onClick = {
                            deps.apps.setHidden(app.packageName, !isHidden)
                            hidden = deps.apps.hiddenPackages()
                        }),
                ) {
                    Text(
                        (if (isHidden) "Hidden  ·  " else "Visible  ·  ") + app.label,
                        color = colors.onSurface, fontSize = 14.sp, modifier = Modifier.padding(12.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun AboutScreen(deps: HearthDependencies, themeRepository: ThemeRepository, onClose: () -> Unit) {
    val colors = LocalHearthColors.current
    val reason = remember { themeRepository.resolutionReason() }
    val config = remember { deps.config.load() }
    val reasonText = when (reason) {
        is ThemeResolutionReason.Manual -> "${reason.themeName} (manually selected)"
        is ThemeResolutionReason.AutomaticSeason -> "${reason.themeName} (Automatic — matches ${reason.from} to ${reason.to})"
        ThemeResolutionReason.AutomaticDefault -> "Default (Automatic — no season matches today)"
    }
    Column(modifier = Modifier.fillMaxSize().background(colors.background.copy(alpha = 0.92f)).padding(32.dp)) {
        Text("About Hearth", color = colors.onSurface, fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Text("Version ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})", color = colors.onSurfaceMuted, fontSize = 14.sp, modifier = Modifier.padding(top = 16.dp))
        Text("Active theme: $reasonText", color = colors.onSurfaceMuted, fontSize = 14.sp, modifier = Modifier.padding(top = 8.dp))
        Text("Device: ${Build.MODEL} · Fire OS / Android ${Build.VERSION.RELEASE}", color = colors.onSurfaceMuted, fontSize = 14.sp, modifier = Modifier.padding(top = 8.dp))
        Text("Configuration", color = colors.onSurface, fontSize = 16.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 20.dp))
        Text("Weather: ${config.weather?.label ?: "not configured"}", color = colors.onSurfaceMuted, fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))
        Text("Guild: ${config.guild?.baseUrl ?: "not configured"}", color = colors.onSurfaceMuted, fontSize = 13.sp)
        Text("Uptime Kuma: ${config.uptimeKuma?.baseUrl ?: "not configured"}", color = colors.onSurfaceMuted, fontSize = 13.sp)
        Text("AdGuard: ${config.adguard?.baseUrl ?: "not configured"}", color = colors.onSurfaceMuted, fontSize = 13.sp)
        Text("Theme catalog: ${config.themeCatalogUrl ?: "not configured"}", color = colors.onSurfaceMuted, fontSize = 13.sp)
    }
}
