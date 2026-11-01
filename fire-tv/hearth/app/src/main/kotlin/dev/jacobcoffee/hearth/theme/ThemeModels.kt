package dev.jacobcoffee.hearth.theme

import kotlinx.serialization.Serializable

/** Mirrors theme.json exactly; see $FT/hearth-spec.md Theme contract. */
@Serializable
data class ThemeJson(
    val schema: Int,
    val id: String,
    val name: String,
    val version: Int,
    val author: String? = null,
    val colors: ThemeColors,
    val wallpaper: String? = null,
    val wallpaperDim: Double? = null,
    /** "drift": a very slow push-in on the wallpaper. Absent or "none": still. */
    val wallpaperMotion: String? = null,
    /** Optional "#RRGGBB": a warm light pool drawn on the background under the focused tile. */
    val focusLight: String? = null,
    val logo: String? = null,
    val fonts: ThemeFonts? = null,
    val displayCaps: Boolean = false,
    val effect: ThemeEffect? = null,
    val tile: ThemeTile? = null,
    val greetings: ThemeGreetings? = null,
    val season: ThemeSeason? = null,
)

@Serializable
data class ThemeColors(
    val background: String,
    val backgroundGradient: List<String>? = null,
    val surface: String,
    val surfaceFocused: String,
    val onSurface: String,
    val onSurfaceMuted: String,
    val accent: String,
    val focusRing: String,
)

@Serializable
data class ThemeFonts(
    val display: String? = null,
    val body: String? = null,
)

enum class ParticleType { none, snow, embers, leaves, fireflies, confetti, hearts, stars, bats, fireworks }

@Serializable
data class ThemeEffect(
    val type: String,
    val density: Double = 0.5,
    val color: String? = null,
) {
    fun particleType(): ParticleType = try {
        ParticleType.valueOf(type)
    } catch (e: IllegalArgumentException) {
        ParticleType.none
    }
}

@Serializable
data class ThemeTile(
    val cornerRadius: Double = 8.0,
    /** "plain" (default) or "chamber"; see ui/TileFrame.kt. */
    val frame: String = "plain",
)

@Serializable
data class ThemeGreetings(
    val morning: String? = null,
    val afternoon: String? = null,
    val evening: String? = null,
    val night: String? = null,
)

/** from/to are "MM-DD", may wrap the year (e.g. 12-31 -> 01-02). */
@Serializable
data class ThemeSeason(
    val from: String,
    val to: String,
    val priority: Int = 0,
)

/** A resolved theme ready to render: JSON plus the directory its relative file paths resolve against. */
data class ResolvedTheme(
    val json: ThemeJson,
    /** Bundled themes resolve under assets/themes/<id>/; downloaded under files/themes/<id>/. */
    val source: ThemeSource,
)

enum class ThemeSource { BUNDLED, DOWNLOADED }
