package dev.jacobcoffee.hearth.apps

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.graphics.drawable.Drawable
import dev.jacobcoffee.hearth.config.AppLayout

data class AppInfo(
    val packageName: String,
    val component: ComponentName,
    val label: String,
    val banner: Drawable?,
    val icon: Drawable,
    /** Installed by the user rather than shipped with Fire OS. */
    val userInstalled: Boolean = false,
)

/**
 * Lists the apps Home can open (TV apps and user-installed apps for the grid, Fire OS's own
 * non-TV launchers for the footer) and persists the user's Hide/Move choices plus launch
 * history, entirely in app-private SharedPreferences -- no PACKAGE_USAGE_STATS permission.
 */
class AppRepository(context: Context) {
    private val appContext = context.applicationContext
    private val prefs = appContext.getSharedPreferences("hearth_apps", Context.MODE_PRIVATE)

    companion object {
        private const val KEY_HIDDEN = "hidden"
        private const val KEY_RECENT = "recent"
        private const val KEY_MANUAL_ORDER = "manual_order"
        private const val KEY_APPLIED_LAYOUT = "applied_layout"
        private const val LAUNCH_COUNT_PREFIX = "count_"
        private const val MAX_RECENT = 12
    }

    /**
     * Every app for the home grid, visible or not: each TV (Leanback) app, plus apps the user
     * installed that only declare a phone-style launcher activity (the Xbox app is one). Amazon's
     * own non-TV system launchers stay out; they are [systemLauncherApps].
     */
    fun homeApps(): List<AppInfo> {
        val leanback = launchable(Intent.CATEGORY_LEANBACK_LAUNCHER, withBanner = true)
        val leanbackPackages = leanback.map { it.packageName }.toSet()
        val installed = launchable(Intent.CATEGORY_LAUNCHER, withBanner = false)
            .filter { it.packageName !in leanbackPackages && it.userInstalled }
        return leanback + installed
    }

    /** Non-TV launchers that ship with Fire OS (Appstore, Silk, Amazon Home, ...): footer links. */
    fun systemLauncherApps(includeHidden: Boolean = false): List<AppInfo> {
        val home = homeApps().map { it.packageName }.toSet()
        val hidden = if (includeHidden) emptySet() else hiddenPackages()
        return launchable(Intent.CATEGORY_LAUNCHER, withBanner = false)
            .filter { it.packageName !in home && it.packageName !in hidden }
    }

    /** Enabled activities answering MAIN + [category], one per package, excluding Hearth. */
    private fun launchable(category: String, withBanner: Boolean): List<AppInfo> {
        val pm = appContext.packageManager
        // No MATCH_DEFAULT_ONLY: Leanback launcher activities declare LEANBACK_LAUNCHER, not
        // DEFAULT, so that flag silently drops almost every TV app.
        val resolved = pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(category), 0)
        return resolved.mapNotNull { ri ->
            val activity = ri.activityInfo ?: return@mapNotNull null
            if (activity.packageName == appContext.packageName) return@mapNotNull null
            // Packages switched off with `pm disable-user` (see packages.txt) never appear.
            if (!activity.applicationInfo.enabled) return@mapNotNull null
            val component = ComponentName(activity.packageName, activity.name)
            val label = ri.loadLabel(pm)?.toString() ?: activity.packageName
            // A banner with no intrinsic size (Fire OS 8.1's Troubleshooting app ships a bare
            // gradient <shape>) has no artwork: the tile shows the icon and label instead.
            val banner = if (!withBanner) null else runCatching {
                pm.getActivityBanner(component) ?: pm.getApplicationBanner(activity.applicationInfo)
            }.getOrNull()?.takeIf { it.intrinsicWidth > 0 && it.intrinsicHeight > 0 }
            val icon = runCatching { ri.loadIcon(pm) }.getOrNull() ?: activity.loadIcon(pm)
            val userInstalled = activity.applicationInfo.flags and ApplicationInfo.FLAG_SYSTEM == 0
            AppInfo(activity.packageName, component, label, banner, icon, userInstalled)
        }.distinctBy { it.packageName }
    }

    /** Visible apps (not hidden) ordered: manually moved first, then recent, then most-used, then alphabetical. */
    fun visibleAppsInOrder(): List<AppInfo> {
        val hidden = hiddenPackages()
        val manualOrder = manualOrderList()
        val recent = recentList()
        val all = homeApps().filterNot { it.packageName in hidden }
        val byPackage = all.associateBy { it.packageName }

        val manualSection = manualOrder.mapNotNull { byPackage[it] }
        val manualSet = manualSection.map { it.packageName }.toSet()

        val remaining = all.filterNot { it.packageName in manualSet }
        val recentIndex = recent.withIndex().associate { (i, pkg) -> pkg to i }
        val remainingSorted = remaining.sortedWith(
            compareBy<AppInfo> { recentIndex[it.packageName] ?: Int.MAX_VALUE }
                .thenByDescending { launchCount(it.packageName) }
                .thenBy { it.label.lowercase() }
        )
        return manualSection + remainingSorted
    }

    fun recordLaunch(packageName: String) {
        val recent = recentList().toMutableList()
        recent.remove(packageName)
        recent.add(0, packageName)
        while (recent.size > MAX_RECENT) recent.removeAt(recent.size - 1)
        prefs.edit()
            .putString(KEY_RECENT, recent.joinToString(","))
            .putInt(LAUNCH_COUNT_PREFIX + packageName, launchCount(packageName) + 1)
            .apply()
    }

    fun launchCount(packageName: String): Int = prefs.getInt(LAUNCH_COUNT_PREFIX + packageName, 0)

    fun hiddenPackages(): Set<String> =
        prefs.getString(KEY_HIDDEN, "")?.split(",")?.filter { it.isNotBlank() }?.toSet() ?: emptySet()

    fun setHidden(packageName: String, hidden: Boolean) {
        val set = hiddenPackages().toMutableSet()
        if (hidden) set.add(packageName) else set.remove(packageName)
        prefs.edit().putString(KEY_HIDDEN, set.joinToString(",")).apply()
    }

    private fun manualOrderList(): List<String> =
        prefs.getString(KEY_MANUAL_ORDER, "")?.split(",")?.filter { it.isNotBlank() } ?: emptyList()

    private fun recentList(): List<String> =
        prefs.getString(KEY_RECENT, "")?.split(",")?.filter { it.isNotBlank() } ?: emptyList()

    /**
     * Applies a pushed [layout] once per distinct layout: its order becomes the manual order
     * and its hidden packages are hidden. Re-pushing the same config changes nothing, so moves
     * made with Arrange on the TV survive until the layout itself is edited.
     */
    fun applyLayout(layout: AppLayout?) {
        if (layout == null) return
        val key = layout.order.joinToString(",") + "|" + layout.hidden.joinToString(",")
        if (prefs.getString(KEY_APPLIED_LAYOUT, null) == key) return
        prefs.edit()
            .putString(KEY_MANUAL_ORDER, layout.order.joinToString(","))
            .putString(KEY_HIDDEN, (hiddenPackages() + layout.hidden).joinToString(","))
            .putString(KEY_APPLIED_LAYOUT, key)
            .apply()
    }

    /** Moves [packageName] to [newIndex] within the manually-ordered front section. */
    fun moveApp(packageName: String, newIndex: Int) {
        val current = visibleAppsInOrder().map { it.packageName }.toMutableList()
        current.remove(packageName)
        val clamped = newIndex.coerceIn(0, current.size)
        current.add(clamped, packageName)
        prefs.edit().putString(KEY_MANUAL_ORDER, current.joinToString(",")).apply()
    }
}
