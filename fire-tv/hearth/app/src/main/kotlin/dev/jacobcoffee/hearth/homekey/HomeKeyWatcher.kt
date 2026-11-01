/*
 * Hearth - a custom Fire TV launcher.
 * Copyright (C) 2026 Hearth contributors
 *
 * This program is free software: you can redistribute it and/or modify it under the terms
 * of the GNU General Public License as published by the Free Software Foundation, either
 * version 3 of the License, or (at your option) any later version. It is distributed in
 * the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
 * warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
 * Public License for more details. You should have received a copy of the GNU General
 * Public License along with this program. If not, see <https://www.gnu.org/licenses/>.
 *
 * Ported from Homestead (https://github.com/Sarg338/Homestead, GPL-3.0) HomeKeyWatcher.java,
 * retargeted to Hearth. Same security posture: only ActivityManager/ActivityTaskManager
 * lines from the system log buffer are read, and a line counts as a Home press only when
 * the log daemon stamps it as written by the system's own uid (1000) -- any app can write
 * a line with any tag/text, but it cannot fake the writer uid the daemon attaches itself.
 */
package dev.jacobcoffee.hearth.homekey

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Message
import android.os.Messenger
import android.os.Process
import android.os.RemoteException
import android.util.Log
import java.io.BufferedReader
import java.io.InputStreamReader
import java.text.ParseException
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale

/**
 * Reports Home presses from the system log. The HOME intent is logged as the system acts
 * on the press, a few hundred ms before Amazon's home draws, and it is the only trace of a
 * press made while Amazon's home is already in front (that changes no window).
 *
 * Needs READ_LOGS, a development permission granted over ADB. The log-buffer grant is
 * applied only when a process starts, so this runs in its own process (`:homekey`):
 * binding after the grant starts a fresh process that can read the log straight away.
 */
class HomeKeyWatcher : Service() {

    companion object {
        const val MSG_REGISTER = 1
        /** arg1 is the uid that started the HOME intent. */
        const val MSG_HOME = 2

        private const val TAG = "Hearth"
        private const val PERM = "android.permission.READ_LOGS"
        /** "-v uid" exists from Android 7. */
        private val UID_FIELD = Build.VERSION.SDK_INT >= Build.VERSION_CODES.N
        private val SYSTEM_UID = Process.SYSTEM_UID.toString()

        /** True once READ_LOGS has been granted, whether or not this process has it yet. */
        @JvmStatic
        fun permitted(c: Context): Boolean =
            c.checkPermission(PERM, Process.myPid(), Process.myUid()) == PackageManager.PERMISSION_GRANTED

        /**
         * The uid that started a HOME intent, or -1 if the line is not one. Visible for
         * testing the line-parsing logic.
         */
        @JvmStatic
        fun homeStartUid(line: String, notBefore: Long, uidField: Boolean): Int {
            if (!line.contains("START u0 {")) return -1
            if (!line.contains("cat=[android.intent.category.HOME]")) return -1
            if (uidField && !writtenBySystem(line)) return -1
            val u = line.indexOf(" from uid ")
            if (u < 0) return -1
            val s = u + 10
            var e = s
            while (e < line.length && line[e].isDigit()) e++
            if (e == s) return -1
            try {
                if (lineTime(line.trim(), System.currentTimeMillis()) < notBefore) return -1
            } catch (ignored: Throwable) {
            }
            return try {
                line.substring(s, e).toInt()
            } catch (t: Throwable) {
                -1
            }
        }

        @JvmStatic
        fun writtenBySystem(line: String): Boolean {
            val t = line.trim()
            val a = t.indexOf(' ')
            if (a < 0) return false
            var b = a
            while (b < t.length && t[b] == ' ') b++
            val e = t.indexOf(' ', b)
            if (e < 0) return false
            var uid = t.substring(b, e)
            if (uid.endsWith(":")) uid = uid.substring(0, uid.length - 1)
            return uid == SYSTEM_UID || uid == "system"
        }

        @JvmStatic
        @Throws(ParseException::class)
        fun lineTime(t: String, now: Long): Long {
            val sp = t.indexOf(' ')
            val first = t.substring(0, sp)
            if (!first.contains('-')) return (first.toDouble() * 1000.0).toLong()
            val rest = t.substring(sp + 1).trim()
            val clock = rest.substring(0, rest.indexOf(' '))
            val f = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US)
            val cal = Calendar.getInstance()
            cal.timeInMillis = now
            val year = cal.get(Calendar.YEAR)
            var at = f.parse("$year-$first $clock")!!.time
            if (at > now + 86400000L) at = f.parse("${year - 1}-$first $clock")!!.time
            return at
        }
    }

    @Volatile private var client: Messenger? = null
    @Volatile private var running = false
    @Volatile private var proc: java.lang.Process? = null
    private var reader: Thread? = null
    private var uidField = UID_FIELD

    private val me = Messenger(object : Handler(Looper.getMainLooper()) {
        override fun handleMessage(m: Message) {
            if (m.what == MSG_REGISTER) client = m.replyTo
        }
    })

    override fun onCreate() {
        super.onCreate()
        if (!permitted(this)) return
        running = true
        reader = Thread({ readLoop() }, "hearth-homekey").also { it.start() }
    }

    override fun onBind(intent: Intent?): IBinder = me.binder

    override fun onDestroy() {
        running = false
        proc?.destroy()
        reader?.interrupt()
        super.onDestroy()
    }

    private fun readLoop() {
        var backoff = 2000L
        while (running) {
            val started = System.currentTimeMillis()
            var lines = 0L
            try {
                val format = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) "epoch" else "time"
                val cmd = mutableListOf("logcat", "-b", "system", "-v", format)
                if (uidField) {
                    cmd.add("-v")
                    cmd.add("uid")
                }
                cmd.addAll(listOf("-T", "1", "ActivityManager:I", "ActivityTaskManager:I", "*:S"))
                val p: java.lang.Process = ProcessBuilder(cmd).redirectErrorStream(true).start()
                proc = p
                val r = BufferedReader(InputStreamReader(p.inputStream))
                Log.i(TAG, "watching for Home presses" + if (uidField) "" else " (no uid stamp)")
                var line: String? = null
                while (running && r.readLine().also { line = it } != null) {
                    lines++
                    val uid = homeStartUid(line!!, started, uidField)
                    if (uid >= 0) send(uid)
                }
            } catch (t: Throwable) {
                if (running) Log.w(TAG, "Home watch failed: $t")
            } finally {
                proc?.destroy()
                proc = null
            }
            if (!running) return
            if (uidField && lines == 0L && System.currentTimeMillis() - started < 3000L) {
                Log.w(TAG, "logcat refused -v uid; reading without the uid stamp")
                uidField = false
                continue
            }
            if (System.currentTimeMillis() - started > 60000L) backoff = 2000L
            try {
                Thread.sleep(backoff)
            } catch (e: InterruptedException) {
                return
            }
            backoff = (backoff * 2).coerceAtMost(60000L)
        }
    }

    private fun send(uid: Int) {
        val c = client ?: return
        try {
            c.send(Message.obtain(null, MSG_HOME, uid, 0))
        } catch (e: RemoteException) {
            client = null
        }
    }
}
