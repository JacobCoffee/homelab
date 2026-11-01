package dev.jacobcoffee.hearth.theme

import android.content.Context
import android.util.Log
import kotlinx.serialization.json.Json
import java.time.MonthDay

/** Why the currently active theme was chosen; shown on the About screen. */
sealed class ThemeResolutionReason {
    data class Manual(val themeName: String) : ThemeResolutionReason()
    data class AutomaticSeason(val themeName: String, val from: String, val to: String) : ThemeResolutionReason()
    object AutomaticDefault : ThemeResolutionReason()
}

/**
 * Loads bundled (`assets/themes/<id>/`) and downloaded (`files/themes/<id>/`) themes, resolves
 * Settings -> Theme ("Automatic" or a fixed id) to one active theme, and persists the user's
 * choice. A downloaded theme overrides a bundled one with the same id only if its version is
 * higher.
 */
class ThemeRepository(context: Context) {
    private val appContext = context.applicationContext
    private val json = Json { ignoreUnknownKeys = true }
    private val prefs = appContext.getSharedPreferences("hearth_theme_prefs", Context.MODE_PRIVATE)

    companion object {
        private const val TAG = "Hearth/Theme"
        private const val KEY_SELECTED = "selected_theme_id"
        private const val ASSETS_ROOT = "themes"
    }

    /** All installed themes (bundled + downloaded, merged), keyed by id. */
    fun loadAll(): Map<String, ResolvedTheme> {
        val bundled = loadBundled()
        val downloaded = loadDownloaded()
        val merged = LinkedHashMap<String, ResolvedTheme>(bundled)
        for ((id, candidate) in downloaded) {
            val existing = merged[id]
            if (existing == null || candidate.json.version > existing.json.version) {
                merged[id] = candidate
            }
        }
        return merged
    }

    private fun loadBundled(): Map<String, ResolvedTheme> {
        val assets = appContext.assets
        val ids = runCatching { assets.list(ASSETS_ROOT)?.toList() ?: emptyList() }.getOrDefault(emptyList())
        val result = LinkedHashMap<String, ResolvedTheme>()
        for (id in ids) {
            val dirPath = "$ASSETS_ROOT/$id"
            val files = runCatching { assets.list(dirPath)?.toSet() ?: emptySet() }.getOrDefault(emptySet())
            if ("theme.json" !in files) continue
            val themeJson = runCatching {
                assets.open("$dirPath/theme.json").bufferedReader().use { it.readText() }
            }.getOrNull() ?: continue
            val parsed = runCatching { json.decodeFromString(ThemeJson.serializer(), themeJson) }.getOrNull()
            if (parsed == null) {
                Log.w(TAG, "bundled theme '$id' has unparsable theme.json")
                continue
            }
            val availableFiles = files - "theme.json"
            when (val v = ThemeValidator.validate(parsed, id, availableFiles)) {
                is ThemeValidation.Invalid -> Log.w(TAG, "bundled theme '$id' invalid: ${v.reason}")
                ThemeValidation.Valid -> result[id] = ResolvedTheme(parsed, ThemeSource.BUNDLED)
            }
        }
        return result
    }

    private fun loadDownloaded(): Map<String, ResolvedTheme> {
        val root = java.io.File(appContext.filesDir, "themes")
        val dirs = root.listFiles { f -> f.isDirectory && !f.name.startsWith(".") } ?: emptyArray()
        val result = LinkedHashMap<String, ResolvedTheme>()
        for (dir in dirs) {
            val themeJsonFile = java.io.File(dir, "theme.json")
            if (!themeJsonFile.isFile) continue
            val parsed = runCatching {
                json.decodeFromString(ThemeJson.serializer(), themeJsonFile.readText())
            }.getOrNull()
            if (parsed == null) {
                Log.w(TAG, "downloaded theme '${dir.name}' has unparsable theme.json")
                continue
            }
            val availableFiles = (dir.list()?.toSet() ?: emptySet()) - "theme.json"
            when (val v = ThemeValidator.validate(parsed, dir.name, availableFiles)) {
                is ThemeValidation.Invalid -> Log.w(TAG, "downloaded theme '${dir.name}' invalid: ${v.reason}")
                ThemeValidation.Valid -> result[dir.name] = ResolvedTheme(parsed, ThemeSource.DOWNLOADED)
            }
        }
        return result
    }

    /** null means "Automatic" (the default). */
    fun selectedThemeId(): String? = prefs.getString(KEY_SELECTED, null)

    fun setSelectedThemeId(id: String?) {
        if (id == null) prefs.edit().remove(KEY_SELECTED).apply()
        else prefs.edit().putString(KEY_SELECTED, id).apply()
    }

    /** Resolves the theme that should be showing right now and updates the fast overlay cache. */
    fun resolveActive(today: MonthDay = MonthDay.now()): ResolvedTheme {
        val all = loadAll()
        val selected = selectedThemeId()
        val resolved = if (selected != null && all.containsKey(selected)) {
            all.getValue(selected)
        } else {
            val id = automaticThemeId(all, today)
            all[id] ?: all[SeasonResolver.DEFAULT_THEME_ID]
                ?: error("themes/default is missing from both assets and downloaded themes")
        }
        ThemeCache.update(appContext, resolved.json)
        return resolved
    }

    private fun automaticThemeId(all: Map<String, ResolvedTheme>, today: MonthDay): String {
        val candidates = all.values.mapNotNull { rt ->
            rt.json.season?.let { s -> SeasonCandidate(rt.json.id, s.from, s.to, s.priority) }
        }
        return SeasonResolver.resolve(candidates, today)
    }

    fun resolutionReason(today: MonthDay = MonthDay.now()): ThemeResolutionReason {
        val all = loadAll()
        val selected = selectedThemeId()
        if (selected != null && all.containsKey(selected)) {
            return ThemeResolutionReason.Manual(all.getValue(selected).json.name)
        }
        val id = automaticThemeId(all, today)
        if (id == SeasonResolver.DEFAULT_THEME_ID && all[id]?.json?.season == null) {
            return ThemeResolutionReason.AutomaticDefault
        }
        val theme = all[id] ?: return ThemeResolutionReason.AutomaticDefault
        val season = theme.json.season ?: return ThemeResolutionReason.AutomaticDefault
        return ThemeResolutionReason.AutomaticSeason(theme.json.name, season.from, season.to)
    }
}
