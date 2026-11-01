package dev.jacobcoffee.hearth.config

import kotlinx.serialization.Serializable

@Serializable
data class HearthConfig(
    val greetingName: String = "there",
    val timezone: String? = null,
    /** Unset widgets stay hidden; config/site.env fills these in via tools/make-config.py. */
    val weather: WeatherConfig? = null,
    val guild: GuildConfig? = null,
    val uptimeKuma: UptimeKumaConfig? = null,
    val adguard: AdGuardConfig? = null,
    val themeCatalogUrl: String? = null,
    /** A home-grid layout to apply once each time a changed config is pushed; see [AppLayout]. */
    val apps: AppLayout? = null,
    /** The TV's listener (tcl-tv/bridge/tcl-listen.sh); null hides every TV control. */
    val tv: TvConfig? = null,
)

@Serializable
data class WeatherConfig(
    val lat: Double,
    val lon: Double,
    val label: String,
    val units: String = "fahrenheit",
)

@Serializable
data class GuildConfig(
    val baseUrl: String,
    val maxEvents: Int = 3,
)

@Serializable
data class UptimeKumaConfig(
    val baseUrl: String,
    val statusPageSlug: String,
)

@Serializable
data class AdGuardConfig(
    val baseUrl: String,
    val username: String,
    val password: String,
)

/**
 * Package names, in grid order, and packages to hide. Applied when a config carrying a
 * different layout arrives; Arrange on the TV can change things afterwards.
 */
@Serializable
data class AppLayout(
    val order: List<String> = emptyList(),
    val hidden: List<String> = emptyList(),
)

@Serializable
data class TvConfig(
    val host: String,
    val port: Int = 47801,
)
