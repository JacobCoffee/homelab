package dev.jacobcoffee.hearth

import dev.jacobcoffee.hearth.theme.ThemeColors
import dev.jacobcoffee.hearth.theme.ThemeEffect
import dev.jacobcoffee.hearth.theme.ThemeFonts
import dev.jacobcoffee.hearth.theme.ThemeJson
import dev.jacobcoffee.hearth.theme.ThemeSeason
import dev.jacobcoffee.hearth.theme.ThemeValidation
import dev.jacobcoffee.hearth.theme.ThemeValidator
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ThemeValidatorTest {

    private val validColors = ThemeColors(
        background = "#0B1A12",
        surface = "#CC14261B",
        surfaceFocused = "#FF2F5A3F",
        onSurface = "#F4F1EA",
        onSurfaceMuted = "#B9C4BC",
        accent = "#D9443B",
        focusRing = "#F2C14E",
    )

    private fun baseTheme(id: String = "christmas") = ThemeJson(
        schema = 1,
        id = id,
        name = "Christmas",
        version = 1,
        colors = validColors,
    )

    @Test
    fun `minimal valid theme passes`() {
        val result = ThemeValidator.validate(baseTheme(), "christmas", emptySet())
        assertEquals(ThemeValidation.Valid, result)
    }

    @Test
    fun `unsupported schema is rejected`() {
        val result = ThemeValidator.validate(baseTheme().copy(schema = 2), "christmas", emptySet())
        assertTrue(result is ThemeValidation.Invalid)
    }

    @Test
    fun `id not matching folder name is rejected`() {
        val result = ThemeValidator.validate(baseTheme(id = "christmas"), "xmas", emptySet())
        assertTrue(result is ThemeValidation.Invalid)
    }

    @Test
    fun `id with invalid characters is rejected`() {
        val result = ThemeValidator.validate(baseTheme(id = "Christmas!"), "Christmas!", emptySet())
        assertTrue(result is ThemeValidation.Invalid)
    }

    @Test
    fun `missing referenced wallpaper file is rejected`() {
        val theme = baseTheme().copy(wallpaper = "wallpaper.webp")
        val result = ThemeValidator.validate(theme, "christmas", emptySet())
        assertTrue(result is ThemeValidation.Invalid)
    }

    @Test
    fun `present referenced wallpaper file passes`() {
        val theme = baseTheme().copy(wallpaper = "wallpaper.webp")
        val result = ThemeValidator.validate(theme, "christmas", setOf("wallpaper.webp"))
        assertEquals(ThemeValidation.Valid, result)
    }

    @Test
    fun `missing referenced font files are rejected`() {
        val theme = baseTheme().copy(fonts = ThemeFonts(display = "display.ttf", body = "body.ttf"))
        val result = ThemeValidator.validate(theme, "christmas", setOf("display.ttf"))
        assertTrue(result is ThemeValidation.Invalid)
    }

    @Test
    fun `invalid hex color is rejected`() {
        val theme = baseTheme().copy(colors = validColors.copy(accent = "red"))
        val result = ThemeValidator.validate(theme, "christmas", emptySet())
        assertTrue(result is ThemeValidation.Invalid)
    }

    @Test
    fun `backgroundGradient with wrong stop count is rejected`() {
        val theme = baseTheme().copy(colors = validColors.copy(backgroundGradient = listOf("#000000")))
        val result = ThemeValidator.validate(theme, "christmas", emptySet())
        assertTrue(result is ThemeValidation.Invalid)
    }

    @Test
    fun `unknown effect type is rejected`() {
        val theme = baseTheme().copy(effect = ThemeEffect(type = "rainbow"))
        val result = ThemeValidator.validate(theme, "christmas", emptySet())
        assertTrue(result is ThemeValidation.Invalid)
    }

    @Test
    fun `known effect type passes`() {
        val theme = baseTheme().copy(effect = ThemeEffect(type = "snow", density = 0.6))
        val result = ThemeValidator.validate(theme, "christmas", emptySet())
        assertEquals(ThemeValidation.Valid, result)
    }

    @Test
    fun `effect density out of range is rejected`() {
        val theme = baseTheme().copy(effect = ThemeEffect(type = "snow", density = 1.5))
        val result = ThemeValidator.validate(theme, "christmas", emptySet())
        assertTrue(result is ThemeValidation.Invalid)
    }

    @Test
    fun `malformed season dates are rejected`() {
        val theme = baseTheme().copy(season = ThemeSeason(from = "13-40", to = "12-26", priority = 50))
        val result = ThemeValidator.validate(theme, "christmas", emptySet())
        assertTrue(result is ThemeValidation.Invalid)
    }

    @Test
    fun `wallpaperDim out of range is rejected`() {
        val theme = baseTheme().copy(wallpaperDim = 1.4)
        val result = ThemeValidator.validate(theme, "christmas", emptySet())
        assertTrue(result is ThemeValidation.Invalid)
    }
}
