/*
 * Hearth - a custom Fire TV launcher.
 * Copyright (C) 2026 Hearth contributors
 * Licensed under the GNU General Public License v3.0 or later.
 *
 * Ported from Homestead (https://github.com/Sarg338/Homestead, GPL-3.0) HomeRedirectService.
 * Same security posture: canRetrieveWindowContent=false, no key filtering, no window
 * content is ever read -- only which package's window came to front.
 *
 * Homestead's upstream design assumes Fire OS refuses background activity starts for ~5 s
 * after a Home press and works around it with a pre-built overlay grid. Measured on this
 * device (Fire OS 8 / AFTKRT), that block does not apply to a start issued from inside a
 * bound AccessibilityService: MainActivity starts immediately. The cover here is kept only
 * to mask the activity's own cold-start layout time (a static branded snapshot matching the
 * resolved theme's background and name, not a duplicate interactive grid -- Hearth's full
 * interactive launcher lives only in the real MainActivity), and is removed shortly after
 * the activity's first frame so nothing is ever left covering another app.
 */
package dev.jacobcoffee.hearth.homekey

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Message
import android.os.Messenger
import android.os.RemoteException
import android.os.SystemClock
import android.util.Log
import android.view.Gravity
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.TextView
import dev.jacobcoffee.hearth.MainActivity
import dev.jacobcoffee.hearth.theme.ThemeCache
import java.lang.ref.WeakReference

class HomeRedirectService : AccessibilityService() {

    companion object {
        private const val TAG = "Hearth"
        private const val AMAZON = "com.amazon.tv.launcher"

        /** Ignores duplicate window events from a single transition. */
        private const val DEBOUNCE_MS = 350L

        /**
         * Measured on-device: unlike a regular app, Fire OS does not apply its ~5 s
         * background-activity-start block to a start issued from a bound accessibility
         * service, so the handoff can be immediate. The cover stays up only long enough to
         * mask the activity's own cold-start layout time.
         */
        private const val HANDOFF_MS = 0L

        /** If the activity still has not taken over by now, try the handoff again. */
        private const val WATCHDOG_MS = 12000L

        /**
         * How long the cover stays after MainActivity reports it has resumed. onResume fires
         * before the first frame is drawn, so lifting at once would show Amazon's home for a
         * flash in between.
         */
        private const val REVEAL_MS = 400L

        @Volatile private var instance: HomeRedirectService? = null

        // Main thread only (accessibility callbacks, activity lifecycle, this Handler).
        private var epoch = 0L
        private var resumedEpoch = -1L
        /** elapsedRealtime, not wall clock: wall clock jumps when NTP syncs after boot. */
        private var suspendUntil = 0L
        private var liveActivity: WeakReference<MainActivity>? = null

        /** Start HomeKeyWatcher once READ_LOGS is granted; safe to call more than once. */
        @JvmStatic
        fun startHomeKeyWatch() {
            instance?.bindWatcher()
        }

        @JvmStatic
        fun suspend(ms: Long) {
            suspendUntil = SystemClock.elapsedRealtime() + ms
        }

        /**
         * Lets Amazon's home screen stay up (the "Amazon Home" link) until Hearth is next in
         * front; [setLiveActivity] re-arms the redirect.
         */
        @JvmStatic
        fun allowAmazonHomeUntilHearthReturns() {
            suspendUntil = Long.MAX_VALUE
        }

        /** MainActivity announces itself so the service knows the real UI has taken over. */
        @JvmStatic
        fun setLiveActivity(a: MainActivity) {
            liveActivity = WeakReference(a)
            if (suspendUntil == Long.MAX_VALUE) suspendUntil = 0L
            resumedEpoch = epoch
            instance?.scheduleHide(REVEAL_MS)
        }

        @JvmStatic
        fun clearLiveActivity(a: MainActivity) {
            if (liveActivity?.get() === a) liveActivity = null
        }
    }

    private val handler = Handler(Looper.getMainLooper())
    private var lastShow = 0L
    private var lastForeground: String? = null
    private var watcherBound = false

    private var wm: WindowManager? = null
    private var cover: FrameLayout? = null
    private var pendingHandoff: Runnable? = null
    private var pendingWatchdog: Runnable? = null
    private var pendingHide: Runnable? = null
    private val bridge by lazy { RemoteBridge(applicationContext) }

    private val watcherConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            val watcherMessenger = Messenger(binder)
            try {
                watcherMessenger.send(
                    Message.obtain(null, HomeKeyWatcher.MSG_REGISTER).apply { replyTo = watcherReplyTo }
                )
            } catch (e: RemoteException) {
                Log.w(TAG, "could not register with HomeKeyWatcher: $e")
            }
        }

        override fun onServiceDisconnected(name: ComponentName) {
            watcherBound = false
        }
    }

    private val watcherReplyTo = Messenger(object : Handler(Looper.getMainLooper()) {
        override fun handleMessage(m: Message) {
            if (m.what == HomeKeyWatcher.MSG_HOME) onHomePressed()
        }
    })

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        bindWatcher()
        bridge.start()
    }

    override fun onUnbind(intent: Intent?): Boolean {
        instance = null
        if (watcherBound) {
            unbindService(watcherConnection)
            watcherBound = false
        }
        detachCover()
        bridge.stop()
        return super.onUnbind(intent)
    }

    private fun bindWatcher() {
        if (watcherBound || !HomeKeyWatcher.permitted(this)) return
        val intent = Intent(this, HomeKeyWatcher::class.java)
        watcherBound = bindService(intent, watcherConnection, Context.BIND_AUTO_CREATE)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (event.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        val pkg = event.packageName?.toString() ?: return
        if (pkg != AMAZON) return
        // Amazon's launcher package also hosts the Fire TV settings screen
        // (ACTION_SETTINGS resolves to .ui.SettingsActivity): only its home activity is Home.
        val cls = event.className?.toString() ?: return
        if (cls != amazonHomeClass) return
        val now = SystemClock.elapsedRealtime()
        if (pkg == lastForeground && now - lastShow < DEBOUNCE_MS) return
        lastForeground = pkg
        lastShow = now
        triggerRedirect()
    }

    /** The activity Fire OS opens for Home, e.g. com.amazon.tv.launcher.ui.HomeActivity_vNext. */
    private val amazonHomeClass: String? by lazy {
        packageManager.resolveActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME), 0)
            ?.activityInfo?.takeIf { it.packageName == AMAZON }?.name
    }

    /** HomeKeyWatcher reports a Home press that caused no window change (already on Amazon's home). */
    private fun onHomePressed() {
        val now = SystemClock.elapsedRealtime()
        if (now - lastShow < DEBOUNCE_MS) return
        lastShow = now
        triggerRedirect()
    }

    private fun triggerRedirect() {
        if (SystemClock.elapsedRealtime() < suspendUntil) return
        epoch++
        val myEpoch = epoch
        Log.i(TAG, "home-redirect-trigger epoch=$myEpoch at=${SystemClock.elapsedRealtime()}")
        showCover()
        cancelHandoff()
        pendingHandoff = Runnable { handoff(myEpoch) }
        handler.postDelayed(pendingHandoff!!, HANDOFF_MS)
        cancelWatchdog()
        pendingWatchdog = Runnable { watchdog(myEpoch) }
        handler.postDelayed(pendingWatchdog!!, WATCHDOG_MS)
    }

    private fun handoff(myEpoch: Long) {
        if (myEpoch != epoch) return
        MainActivity.open(this)
    }

    private fun watchdog(myEpoch: Long) {
        if (myEpoch != epoch) return
        if (resumedEpoch == myEpoch) return
        // The activity never took over; try again.
        MainActivity.open(this)
    }

    private fun scheduleHide(delayMs: Long) {
        cancelHide()
        val r = Runnable { detachCover() }
        pendingHide = r
        handler.postDelayed(r, delayMs)
    }

    override fun onInterrupt() {}

    // ------------------------------------------------------------- cover window

    private fun showCover() {
        val windowManager = wm ?: return
        if (cover != null) return
        val snapshot = CoverSnapshot.load(this)
        val view = FrameLayout(this).apply {
            setBackgroundColor(ThemeCache.cachedBackgroundColor(this@HomeRedirectService))
            if (snapshot != null) {
                // The home screen as it last looked: Home lands on it without a placeholder.
                addView(
                    ImageView(this@HomeRedirectService).apply {
                        setImageBitmap(snapshot)
                        scaleType = ImageView.ScaleType.CENTER_CROP
                    },
                    FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT),
                )
            } else {
                // Before the first capture (just installed): the theme's color and name, centered.
                addView(
                    TextView(this@HomeRedirectService).apply {
                        text = ThemeCache.cachedDisplayName(this@HomeRedirectService).uppercase()
                        setTextColor(ThemeCache.cachedAccentColor(this@HomeRedirectService))
                        textSize = 26f
                        letterSpacing = 0.3f
                    },
                    FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT)
                        .apply { gravity = Gravity.CENTER },
                )
            }
        }
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.OPAQUE
        )
        try {
            windowManager.addView(view, lp)
            cover = view
        } catch (t: Throwable) {
            Log.w(TAG, "could not attach cover: $t")
        }
    }

    private fun detachCover() {
        cancelHide()
        val windowManager = wm
        val view = cover
        cover = null
        if (windowManager != null && view != null) {
            try {
                windowManager.removeView(view)
            } catch (ignored: Throwable) {
            }
        }
    }

    private fun cancelHandoff() {
        pendingHandoff?.let { handler.removeCallbacks(it) }
        pendingHandoff = null
    }

    private fun cancelWatchdog() {
        pendingWatchdog?.let { handler.removeCallbacks(it) }
        pendingWatchdog = null
    }

    private fun cancelHide() {
        pendingHide?.let { handler.removeCallbacks(it) }
        pendingHide = null
    }
}
