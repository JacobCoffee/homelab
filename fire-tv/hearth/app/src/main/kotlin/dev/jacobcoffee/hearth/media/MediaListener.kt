package dev.jacobcoffee.hearth.media

import android.service.notification.NotificationListenerService

/**
 * Exists only so Android lets Hearth see other apps' media sessions:
 * MediaSessionManager.getActiveSessions needs an enabled notification listener (or a
 * system-only permission). It reads no notifications. tools/install.sh enables it with
 * `cmd notification allow_listener`.
 */
class MediaListener : NotificationListenerService()
