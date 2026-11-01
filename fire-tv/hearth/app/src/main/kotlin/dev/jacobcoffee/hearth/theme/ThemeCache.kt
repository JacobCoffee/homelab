package dev.jacobcoffee.hearth.theme

import android.content.Context
import android.graphics.Color

/**
 * A tiny SharedPreferences mirror of "what theme is active right now", written by
 * [ThemeRepository] whenever it resolves a theme and read synchronously by
 * HomeRedirectService's cover window, which must not touch disk-heavy asset/zip parsing on
 * the Home-press critical path.
 */
object ThemeCache {
    private const val FILE = "hearth_theme_cache"
    private const val KEY_BACKGROUND = "background"
    private const val KEY_ACCENT = "accent"
    private const val KEY_NAME = "name"

    private const val DEFAULT_BACKGROUND = "#0B0E11"
    private const val DEFAULT_ACCENT = "#D9443B"
    private const val DEFAULT_NAME = "Hearth"

    fun update(context: Context, theme: ThemeJson) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .putString(KEY_BACKGROUND, theme.colors.background)
            .putString(KEY_ACCENT, theme.colors.accent)
            .putString(KEY_NAME, theme.name)
            .apply()
    }

    fun cachedBackgroundColor(context: Context): Int = parseColorOr(
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString(KEY_BACKGROUND, null),
        DEFAULT_BACKGROUND,
    )

    fun cachedAccentColor(context: Context): Int = parseColorOr(
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString(KEY_ACCENT, null),
        DEFAULT_ACCENT,
    )

    fun cachedDisplayName(context: Context): String =
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString(KEY_NAME, null) ?: DEFAULT_NAME

    private fun parseColorOr(hex: String?, fallback: String): Int = try {
        Color.parseColor(hex ?: fallback)
    } catch (e: IllegalArgumentException) {
        Color.parseColor(fallback)
    }
}
