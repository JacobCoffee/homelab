package dev.jacobcoffee.hearth

import dev.jacobcoffee.hearth.catalog.CatalogInstaller
import dev.jacobcoffee.hearth.catalog.InstallOutcome
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayOutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class CatalogInstallerTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun zipOf(vararg entries: Pair<String, String>): ByteArray {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { zos ->
            for ((name, content) in entries) {
                zos.putNextEntry(ZipEntry(name))
                zos.write(content.toByteArray())
                zos.closeEntry()
            }
        }
        return out.toByteArray()
    }

    private val validThemeJson = """
        {"schema":1,"id":"christmas","name":"Christmas","version":1,
         "colors":{"background":"#0B1A12","surface":"#CC14261B","surfaceFocused":"#FF2F5A3F",
         "onSurface":"#F4F1EA","onSurfaceMuted":"#B9C4BC","accent":"#D9443B","focusRing":"#F2C14E"}}
    """.trimIndent()

    @Test
    fun `size mismatch is rejected before extraction`() {
        val bytes = zipOf("theme.json" to validThemeJson)
        val result = CatalogInstaller.install(
            bytes, CatalogInstaller.sha256Hex(bytes), expectedSize = bytes.size.toLong() + 1,
            id = "christmas", themesDir = tmp.newFolder("themes"),
        )
        assertTrue(result is InstallOutcome.Rejected)
        assertTrue((result as InstallOutcome.Rejected).reason.contains("size mismatch"))
    }

    @Test
    fun `sha256 mismatch is rejected`() {
        val bytes = zipOf("theme.json" to validThemeJson)
        val result = CatalogInstaller.install(
            bytes, expectedSha256 = "0".repeat(64), expectedSize = bytes.size.toLong(),
            id = "christmas", themesDir = tmp.newFolder("themes"),
        )
        assertTrue(result is InstallOutcome.Rejected)
        assertTrue((result as InstallOutcome.Rejected).reason.contains("sha256 mismatch"))
    }

    @Test
    fun `valid archive installs atomically`() {
        val bytes = zipOf("theme.json" to validThemeJson)
        val themesDir = tmp.newFolder("themes")
        val result = CatalogInstaller.install(
            bytes, CatalogInstaller.sha256Hex(bytes), bytes.size.toLong(), "christmas", themesDir,
        )
        assertEquals(InstallOutcome.Installed("christmas", 1), result)
        assertTrue(java.io.File(themesDir, "christmas/theme.json").isFile)
        // No leftover staging/temp directories.
        assertEquals(listOf("christmas"), themesDir.list()?.toList())
    }

    @Test
    fun `zip-slip parent traversal entry is rejected`() {
        val bytes = zipOf("../evil.txt" to "pwned", "theme.json" to validThemeJson)
        val themesDir = tmp.newFolder("themes")
        val result = CatalogInstaller.install(
            bytes, CatalogInstaller.sha256Hex(bytes), bytes.size.toLong(), "christmas", themesDir,
        )
        assertTrue(result is InstallOutcome.Rejected)
        assertTrue((result as InstallOutcome.Rejected).reason.contains("zip entry escapes"))
        // Nothing was written outside the themes dir.
        assertTrue(!java.io.File(themesDir.parentFile, "evil.txt").exists())
    }

    @Test
    fun `zip-slip absolute path entry is rejected`() {
        val bytes = zipOf("/etc/passwd" to "pwned", "theme.json" to validThemeJson)
        val themesDir = tmp.newFolder("themes")
        val result = CatalogInstaller.install(
            bytes, CatalogInstaller.sha256Hex(bytes), bytes.size.toLong(), "christmas", themesDir,
        )
        assertTrue(result is InstallOutcome.Rejected)
    }

    @Test
    fun `missing theme json at zip root is rejected`() {
        val bytes = zipOf("nested/theme.json" to validThemeJson)
        val themesDir = tmp.newFolder("themes")
        val result = CatalogInstaller.install(
            bytes, CatalogInstaller.sha256Hex(bytes), bytes.size.toLong(), "christmas", themesDir,
        )
        assertTrue(result is InstallOutcome.Rejected)
        assertTrue((result as InstallOutcome.Rejected).reason.contains("theme.json"))
    }

    @Test
    fun `theme json id mismatch with catalog entry is rejected`() {
        val bytes = zipOf("theme.json" to validThemeJson)
        val themesDir = tmp.newFolder("themes")
        val result = CatalogInstaller.install(
            bytes, CatalogInstaller.sha256Hex(bytes), bytes.size.toLong(), "not-christmas", themesDir,
        )
        assertTrue(result is InstallOutcome.Rejected)
        assertTrue((result as InstallOutcome.Rejected).reason.contains("does not match"))
    }

    @Test
    fun `referenced file missing from archive is rejected`() {
        val themeWithWallpaper = """
            {"schema":1,"id":"christmas","name":"Christmas","version":1,"wallpaper":"wallpaper.webp",
             "colors":{"background":"#0B1A12","surface":"#CC14261B","surfaceFocused":"#FF2F5A3F",
             "onSurface":"#F4F1EA","onSurfaceMuted":"#B9C4BC","accent":"#D9443B","focusRing":"#F2C14E"}}
        """.trimIndent()
        val bytes = zipOf("theme.json" to themeWithWallpaper)
        val themesDir = tmp.newFolder("themes")
        val result = CatalogInstaller.install(
            bytes, CatalogInstaller.sha256Hex(bytes), bytes.size.toLong(), "christmas", themesDir,
        )
        assertTrue(result is InstallOutcome.Rejected)
    }
}
