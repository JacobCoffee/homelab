package dev.jacobcoffee.hearth.catalog

import dev.jacobcoffee.hearth.theme.ThemeJson
import dev.jacobcoffee.hearth.theme.ThemeValidator
import kotlinx.serialization.json.Json
import java.io.ByteArrayInputStream
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.zip.ZipInputStream

sealed class InstallOutcome {
    data class Installed(val id: String, val version: Int) : InstallOutcome()
    data class Rejected(val reason: String) : InstallOutcome()
}

/**
 * Verifies and installs a downloaded theme zip into app-private storage. Pure file-system
 * logic (no Android/network dependency) so the edge cases -- zip-slip, hash mismatch -- are
 * directly unit-testable on the JVM.
 */
object CatalogInstaller {
    private val json = Json { ignoreUnknownKeys = true }

    fun sha256Hex(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
        return digest.joinToString("") { "%02x".format(it) }
    }

    /**
     * @param zipBytes the downloaded archive.
     * @param expectedSha256 hex sha256 from the catalog index, case-insensitive.
     * @param expectedSize byte size from the catalog index.
     * @param id the catalog entry's theme id; the zip's theme.json must declare this id.
     * @param themesDir app-private root (e.g. filesDir/themes); the theme lands at themesDir/id.
     */
    fun install(
        zipBytes: ByteArray,
        expectedSha256: String,
        expectedSize: Long,
        id: String,
        themesDir: File,
    ): InstallOutcome {
        if (zipBytes.size.toLong() != expectedSize) {
            return InstallOutcome.Rejected("size mismatch: expected $expectedSize, got ${zipBytes.size}")
        }
        val actualSha = sha256Hex(zipBytes)
        if (!actualSha.equals(expectedSha256, ignoreCase = true)) {
            return InstallOutcome.Rejected("sha256 mismatch: expected $expectedSha256, got $actualSha")
        }

        themesDir.mkdirs()
        val tempDir = File(themesDir, ".tmp-$id-${System.nanoTime()}")
        tempDir.mkdirs()
        try {
            val extractedNames = try {
                extractZipSafely(zipBytes, tempDir)
            } catch (e: ZipSlipException) {
                return InstallOutcome.Rejected("zip entry escapes the theme directory: ${e.entryName}")
            } catch (e: IOException) {
                return InstallOutcome.Rejected("could not extract archive: ${e.message}")
            }

            val themeJsonFile = File(tempDir, "theme.json")
            if (!themeJsonFile.isFile) return InstallOutcome.Rejected("zip has no theme.json at its root")
            val parsed = try {
                json.decodeFromString(ThemeJson.serializer(), themeJsonFile.readText())
            } catch (e: Exception) {
                return InstallOutcome.Rejected("theme.json is not valid: ${e.message}")
            }
            if (parsed.id != id) {
                return InstallOutcome.Rejected("theme.json id '${parsed.id}' does not match catalog entry '$id'")
            }

            val availableFiles = extractedNames.filterNot { it == "theme.json" }.toSet()
            when (val validation = ThemeValidator.validate(parsed, id, availableFiles)) {
                is dev.jacobcoffee.hearth.theme.ThemeValidation.Invalid ->
                    return InstallOutcome.Rejected("theme.json invalid: ${validation.reason}")
                dev.jacobcoffee.hearth.theme.ThemeValidation.Valid -> Unit
            }

            val finalDir = File(themesDir, id)
            val staging = File(themesDir, "$id-${parsed.version}")
            if (staging.exists()) staging.deleteRecursively()
            if (!tempDir.renameTo(staging)) {
                return InstallOutcome.Rejected("could not stage install directory")
            }
            // Atomic swap: remove the old version only after the new one is fully in place.
            if (finalDir.exists()) finalDir.deleteRecursively()
            if (!staging.renameTo(finalDir)) {
                staging.deleteRecursively()
                return InstallOutcome.Rejected("could not activate installed theme")
            }
            return InstallOutcome.Installed(id, parsed.version)
        } finally {
            if (tempDir.exists()) tempDir.deleteRecursively()
        }
    }

    class ZipSlipException(val entryName: String) : IOException("zip-slip entry: $entryName")

    /** Returns the relative paths extracted. Rejects absolute paths and ".." traversal. */
    fun extractZipSafely(zipBytes: ByteArray, destDir: File): List<String> {
        val canonicalDest = destDir.canonicalFile
        val extracted = mutableListOf<String>()
        ZipInputStream(ByteArrayInputStream(zipBytes)).use { zis ->
            var entry = zis.nextEntry
            while (entry != null) {
                val name = entry.name
                if (name.startsWith("/") || name.startsWith("\\") || name.contains("..")) {
                    throw ZipSlipException(name)
                }
                val outFile = File(destDir, name)
                val canonicalOut = outFile.canonicalFile
                if (!canonicalOut.path.startsWith(canonicalDest.path + File.separator) &&
                    canonicalOut != canonicalDest
                ) {
                    throw ZipSlipException(name)
                }
                if (entry.isDirectory) {
                    outFile.mkdirs()
                } else {
                    outFile.parentFile?.mkdirs()
                    outFile.outputStream().use { out -> zis.copyTo(out) }
                    extracted += name
                }
                zis.closeEntry()
                entry = zis.nextEntry
            }
        }
        return extracted
    }
}
