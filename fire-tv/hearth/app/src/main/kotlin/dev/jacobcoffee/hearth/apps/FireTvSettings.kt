package dev.jacobcoffee.hearth.apps

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.provider.Settings
import dev.jacobcoffee.hearth.homekey.HomeRedirectService

/**
 * Opens the real Fire TV settings screen. An app's implicit ACTION_SETTINGS needs CATEGORY_DEFAULT,
 * which only Amazon's CTSDummySettingsHandler declares: it drops you on Amazon's home screen
 * instead. The launcher's own SettingsActivity answers ACTION_SETTINGS without DEFAULT, so it is
 * started by name. It is a trampoline into a page of Amazon's home activity, so the Home
 * redirect stands down until Hearth is next in front (Guide on the TCL remote brings it back).
 */
object FireTvSettings {
    private const val PACKAGE = "com.amazon.tv.launcher"
    private const val ACTIVITY = "com.amazon.tv.launcher.ui.SettingsActivity"

    fun open(context: Context) {
        HomeRedirectService.allowAmazonHomeUntilHearthReturns()
        val explicit = Intent(Settings.ACTION_SETTINGS).setClassName(PACKAGE, ACTIVITY).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            context.startActivity(explicit)
        } catch (e: ActivityNotFoundException) {
            context.startActivity(Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: SecurityException) {
            context.startActivity(Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }
}
