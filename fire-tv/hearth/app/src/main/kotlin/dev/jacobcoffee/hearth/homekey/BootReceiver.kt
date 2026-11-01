/*
 * Hearth - a custom Fire TV launcher.
 * Copyright (C) 2026 Hearth contributors
 * Licensed under the GNU General Public License v3.0 or later.
 *
 * Ported from Homestead (https://github.com/Sarg338/Homestead, GPL-3.0) BootReceiver.java,
 * retargeted to Hearth.
 */
package dev.jacobcoffee.hearth.homekey

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import dev.jacobcoffee.hearth.MainActivity

/**
 * Opens Hearth at boot so Amazon's home screen is not drawn first. The accessibility
 * service would replace it anyway; this just avoids showing it at all.
 *
 * Also reopens it after an update started from the app: replacing the app closes it and
 * leaves the user on Amazon's home screen, where Home does nothing until the service
 * rebinds.
 */
class BootReceiver : BroadcastReceiver() {

    companion object {
        /** How long after starting an install the replace still counts as ours. */
        private const val UPDATE_WINDOW_MS = 10L * 60L * 1000L
    }

    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action
        val boot = Intent.ACTION_BOOT_COMPLETED == action
        val updated = Intent.ACTION_MY_PACKAGE_REPLACED == action
        if (!boot && !updated) return
        if (updated) {
            val started = Prefs.updateStarted(context)
            Prefs.setUpdateStarted(context, 0L)
            if (started == 0L || System.currentTimeMillis() - started > UPDATE_WINDOW_MS) return
        }
        // The user turned the Home button off: leave them on Amazon's home screen.
        if (Prefs.homeOffByUser(context)) return

        val appContext = context.applicationContext
        Handler(Looper.getMainLooper()).postDelayed({
            MainActivity.open(appContext)
        }, if (boot) 6000L else 1500L)
    }
}
