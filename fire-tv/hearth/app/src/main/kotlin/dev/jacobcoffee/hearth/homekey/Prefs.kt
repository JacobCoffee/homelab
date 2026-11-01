package dev.jacobcoffee.hearth.homekey

import android.content.Context

/** Small SharedPreferences wrapper for the home-redirect machinery. */
object Prefs {
    private const val FILE = "hearth_homekey"
    private const val KEY_HOME_OFF = "home_off_by_user"
    private const val KEY_UPDATE_STARTED = "update_started"

    private fun prefs(c: Context) = c.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun homeOffByUser(c: Context): Boolean = prefs(c).getBoolean(KEY_HOME_OFF, false)

    fun setHomeOffByUser(c: Context, off: Boolean) {
        prefs(c).edit().putBoolean(KEY_HOME_OFF, off).apply()
    }

    fun updateStarted(c: Context): Long = prefs(c).getLong(KEY_UPDATE_STARTED, 0L)

    fun setUpdateStarted(c: Context, time: Long) {
        prefs(c).edit().putLong(KEY_UPDATE_STARTED, time).apply()
    }
}
