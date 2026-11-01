package dev.jacobcoffee.hearth.widgets

import android.util.Log
import dev.jacobcoffee.hearth.net.NetworkClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

data class DailyForecast(val date: String, val maxF: Double, val minF: Double, val weatherCode: Int)

data class WeatherSnapshot(
    val currentTempF: Double,
    val weatherCode: Int,
    val isDay: Boolean,
    val daily: List<DailyForecast>,
    val fetchedAtMillis: Long,
)

/** Last good value plus whether it is stale, for the "age marker" the contract requires on failure. */
sealed class WeatherState {
    object Loading : WeatherState()
    data class Available(val snapshot: WeatherSnapshot, val stale: Boolean) : WeatherState()
    object Unavailable : WeatherState()
}

@Serializable
private data class OpenMeteoResponse(
    val current: CurrentBlock,
    val daily: DailyBlock,
)

@Serializable
private data class CurrentBlock(
    @SerialName("temperature_2m") val temperature2m: Double,
    @SerialName("weather_code") val weatherCode: Int,
    @SerialName("is_day") val isDay: Int,
)

@Serializable
private data class DailyBlock(
    val time: List<String>,
    @SerialName("temperature_2m_max") val temperature2mMax: List<Double>,
    @SerialName("temperature_2m_min") val temperature2mMin: List<Double>,
    @SerialName("weather_code") val weatherCode: List<Int>,
)

class WeatherRepository {
    private val json = Json { ignoreUnknownKeys = true }
    @Volatile private var last: WeatherSnapshot? = null

    suspend fun fetch(lat: Double, lon: Double, units: String): WeatherState = withContext(Dispatchers.IO) {
        val tempUnit = if (units == "celsius") "celsius" else "fahrenheit"
        val url = "https://api.open-meteo.com/v1/forecast" +
            "?latitude=$lat&longitude=$lon" +
            "&current=temperature_2m,weather_code,is_day" +
            "&daily=temperature_2m_max,temperature_2m_min,weather_code" +
            "&temperature_unit=$tempUnit&timezone=auto&forecast_days=3"
        try {
            val body = NetworkClient.client.newCall(NetworkClient.get(url)).execute().use { resp ->
                if (!resp.isSuccessful) throw java.io.IOException("HTTP ${resp.code}")
                resp.body?.string() ?: throw java.io.IOException("empty response")
            }
            val parsed = json.decodeFromString(OpenMeteoResponse.serializer(), body)
            val daily = parsed.daily.time.indices.take(3).map { i ->
                DailyForecast(
                    date = parsed.daily.time[i],
                    maxF = parsed.daily.temperature2mMax[i],
                    minF = parsed.daily.temperature2mMin[i],
                    weatherCode = parsed.daily.weatherCode[i],
                )
            }
            val snapshot = WeatherSnapshot(
                currentTempF = parsed.current.temperature2m,
                weatherCode = parsed.current.weatherCode,
                isDay = parsed.current.isDay == 1,
                daily = daily,
                fetchedAtMillis = System.currentTimeMillis(),
            )
            last = snapshot
            WeatherState.Available(snapshot, stale = false)
        } catch (e: Exception) {
            Log.w("Hearth/Weather", "fetch failed: $e")
            val cached = last
            if (cached != null) WeatherState.Available(cached, stale = true) else WeatherState.Unavailable
        }
    }
}
