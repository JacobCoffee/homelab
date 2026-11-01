/*
 * Hearth - a custom Fire TV launcher.
 * Copyright (C) 2026 Hearth contributors
 * Licensed under the GNU General Public License v3.0 or later.
 *
 * Pattern follows Homestead's BackgroundProvider.java (same project, GPL-3.0): a write-only
 * ContentProvider reached from a computer with
 *   adb push hearth.json /data/local/tmp/
 *   adb shell "content write --uri content://dev.jacobcoffee.hearth.config/config
 *       < /data/local/tmp/hearth.json"
 * Only the adb shell uid and root may write; everyone else is refused. Nothing can be read
 * back through it.
 */
package dev.jacobcoffee.hearth.config

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.net.Uri
import android.os.Binder
import android.os.ParcelFileDescriptor
import android.util.Log
import java.io.File
import java.io.FileNotFoundException
import java.io.FileOutputStream
import java.io.IOException

class ConfigProvider : ContentProvider() {

    companion object {
        const val AUTHORITY = "dev.jacobcoffee.hearth.config"
        private const val TAG = "Hearth/Config"
        private const val SHELL_UID = 2000
        private const val ROOT_UID = 0
        private const val MAX_BYTES = 1L * 1024L * 1024L // config.json is small
    }

    override fun onCreate(): Boolean = true

    private fun checkCaller() {
        val uid = Binder.getCallingUid()
        if (uid != SHELL_UID && uid != ROOT_UID) {
            throw SecurityException("only adb shell may push Hearth config")
        }
    }

    private fun isConfig(uri: Uri?): Boolean = uri != null && uri.path == "/config"

    /**
     * Hands the caller the write end of a pipe. A worker copies what arrives into a
     * temporary file, validates it as JSON matching the config contract, and only then
     * replaces the stored config -- a malformed or cut-off push never corrupts the running
     * configuration.
     */
    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        checkCaller()
        if (!isConfig(uri)) throw FileNotFoundException("only $AUTHORITY/config")
        if (mode.contains("r")) throw FileNotFoundException("write only")
        val pipe = try {
            ParcelFileDescriptor.createPipe()
        } catch (e: IOException) {
            throw FileNotFoundException(e.toString())
        }
        val part = File(context!!.filesDir, "config.json.part")
        Thread({
            var total = 0L
            var ok = false
            ParcelFileDescriptor.AutoCloseInputStream(pipe[0]).use { input ->
                try {
                    FileOutputStream(part).use { out ->
                        val buf = ByteArray(65536)
                        var n = input.read(buf)
                        while (n > 0) {
                            total += n
                            if (total > MAX_BYTES) throw IOException("config larger than ${MAX_BYTES / 1024} KB")
                            out.write(buf, 0, n)
                            n = input.read(buf)
                        }
                    }
                    val repo = ConfigRepository(context!!)
                    val saved = repo.save(part.readText())
                    ok = saved
                    when (saved) {
                        false -> Log.w(TAG, "pushed config failed JSON validation")
                        true -> Unit
                    }
                } catch (t: Throwable) {
                    Log.w(TAG, "config not received: $t")
                } finally {
                    part.delete()
                }
            }
            Log.i(TAG, if (ok) "config received, $total bytes" else "config discarded")
        }, "hearth-config-in").start()
        return pipe[1]
    }

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, args: Array<out String>?, sortOrder: String?): Cursor? = null
    override fun getType(uri: Uri): String? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? {
        checkCaller()
        return null
    }
    override fun update(uri: Uri, values: ContentValues?, selection: String?, args: Array<out String>?): Int = 0
    override fun delete(uri: Uri, selection: String?, args: Array<out String>?): Int = 0
}
