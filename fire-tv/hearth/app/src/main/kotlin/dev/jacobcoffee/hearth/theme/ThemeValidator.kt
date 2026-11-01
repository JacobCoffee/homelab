package dev.jacobcoffee.hearth.theme

sealed class ThemeValidation {
    object Valid : ThemeValidation()
    data class Invalid(val reason: String) : ThemeValidation()
}

/**
 * Validates a parsed theme.json against the contract and the set of files actually present
 * alongside it. Pure logic, no file-system or Android dependency, so it is directly
 * unit-testable.
 */
object ThemeValidator {
    private val ID_PATTERN = Regex("[a-z0-9-]+")
    private val HEX_COLOR = Regex("#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})")
    private val WALLPAPER_MOTIONS = setOf("none", "drift")
    private val TILE_FRAMES = setOf("plain", "chamber")

    /**
     * @param folderId the folder/zip-root name the theme was loaded from; must equal json.id.
     * @param availableFiles file names present next to theme.json (relative paths, as named
     *   by wallpaper/logo/fonts.display/fonts.body).
     */
    fun validate(json: ThemeJson, folderId: String, availableFiles: Set<String>): ThemeValidation {
        if (json.schema != 1) return ThemeValidation.Invalid("unsupported schema ${json.schema}")
        if (!ID_PATTERN.matches(json.id)) return ThemeValidation.Invalid("id '${json.id}' must match [a-z0-9-]+")
        if (json.id != folderId) return ThemeValidation.Invalid("id '${json.id}' must equal folder name '$folderId'")
        if (json.version < 1) return ThemeValidation.Invalid("version must be >= 1")

        for ((label, hex) in listOf(
            "colors.background" to json.colors.background,
            "colors.surface" to json.colors.surface,
            "colors.surfaceFocused" to json.colors.surfaceFocused,
            "colors.onSurface" to json.colors.onSurface,
            "colors.onSurfaceMuted" to json.colors.onSurfaceMuted,
            "colors.accent" to json.colors.accent,
            "colors.focusRing" to json.colors.focusRing,
        )) {
            if (!HEX_COLOR.matches(hex)) return ThemeValidation.Invalid("$label '$hex' is not #RRGGBB or #AARRGGBB")
        }
        json.colors.backgroundGradient?.let { stops ->
            if (stops.size !in 2..3) return ThemeValidation.Invalid("colors.backgroundGradient needs 2-3 stops")
            stops.forEachIndexed { i, hex ->
                if (!HEX_COLOR.matches(hex)) {
                    return ThemeValidation.Invalid("colors.backgroundGradient[$i] '$hex' is not a valid color")
                }
            }
        }
        json.wallpaperDim?.let {
            if (it < 0.0 || it > 1.0) return ThemeValidation.Invalid("wallpaperDim must be 0..1")
        }
        json.wallpaperMotion?.let {
            if (it !in WALLPAPER_MOTIONS) return ThemeValidation.Invalid("wallpaperMotion must be one of $WALLPAPER_MOTIONS")
        }
        json.focusLight?.let {
            if (!HEX_COLOR.matches(it)) return ThemeValidation.Invalid("focusLight '$it' is not a valid color")
        }
        json.tile?.let {
            if (it.frame !in TILE_FRAMES) return ThemeValidation.Invalid("tile.frame must be one of $TILE_FRAMES")
        }

        json.wallpaper?.let {
            if (it !in availableFiles) return ThemeValidation.Invalid("wallpaper '$it' is missing from the theme")
        }
        json.logo?.let {
            if (it !in availableFiles) return ThemeValidation.Invalid("logo '$it' is missing from the theme")
        }
        json.fonts?.display?.let {
            if (it !in availableFiles) return ThemeValidation.Invalid("fonts.display '$it' is missing from the theme")
        }
        json.fonts?.body?.let {
            if (it !in availableFiles) return ThemeValidation.Invalid("fonts.body '$it' is missing from the theme")
        }

        json.effect?.let {
            if (runCatching { ParticleType.valueOf(it.type) }.isFailure) {
                return ThemeValidation.Invalid("effect.type '${it.type}' is not a known particle type")
            }
            if (it.density < 0.0 || it.density > 1.0) {
                return ThemeValidation.Invalid("effect.density must be 0..1")
            }
        }

        json.season?.let { season ->
            val from = runCatching { SeasonResolver.parseMonthDay(season.from) }
                .getOrElse { return ThemeValidation.Invalid("season.from '${season.from}' is not MM-DD") }
            runCatching { SeasonResolver.parseMonthDay(season.to) }
                .getOrElse { return ThemeValidation.Invalid("season.to '${season.to}' is not MM-DD") }
            from // referenced to avoid unused warning
        }

        return ThemeValidation.Valid
    }
}
