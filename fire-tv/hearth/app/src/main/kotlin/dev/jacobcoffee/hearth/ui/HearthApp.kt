package dev.jacobcoffee.hearth.ui

import androidx.activity.compose.BackHandler
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import dev.jacobcoffee.hearth.apps.AppRepository
import dev.jacobcoffee.hearth.catalog.CatalogClient
import dev.jacobcoffee.hearth.config.ConfigRepository
import dev.jacobcoffee.hearth.theme.ThemeRepository

sealed class Screen {
    object Launcher : Screen()
    data class Settings(val themesOnly: Boolean = false) : Screen()
}

class HearthDependencies(context: android.content.Context) {
    val config = ConfigRepository(context)
    val apps = AppRepository(context)
    val catalog = CatalogClient(context)
}

@Composable
fun HearthApp(
    themeRepository: ThemeRepository,
    reduceMotion: Boolean,
    onReduceMotionChanged: (Boolean) -> Unit,
    onThemeChanged: () -> Unit,
) {
    val context = LocalContext.current
    val deps = remember { HearthDependencies(context) }
    var screen by remember { mutableStateOf<Screen>(Screen.Launcher) }

    BackHandler(enabled = screen is Screen.Settings) { screen = Screen.Launcher }

    when (val s = screen) {
        Screen.Launcher -> LauncherScreen(
            deps = deps,
            onOpenSettings = { themesOnly -> screen = Screen.Settings(themesOnly) },
        )
        is Screen.Settings -> SettingsScreen(
            deps = deps,
            themeRepository = themeRepository,
            reduceMotion = reduceMotion,
            onReduceMotionChanged = onReduceMotionChanged,
            onThemeChanged = onThemeChanged,
            onClose = { screen = Screen.Launcher },
            themesOnly = s.themesOnly,
        )
    }
}

object ReduceMotionStore {
    private const val FILE = "hearth_prefs"
    private const val KEY = "reduce_motion"
    fun get(context: android.content.Context): Boolean =
        context.getSharedPreferences(FILE, android.content.Context.MODE_PRIVATE).getBoolean(KEY, false)
    fun set(context: android.content.Context, value: Boolean) {
        context.getSharedPreferences(FILE, android.content.Context.MODE_PRIVATE).edit().putBoolean(KEY, value).apply()
    }
}
