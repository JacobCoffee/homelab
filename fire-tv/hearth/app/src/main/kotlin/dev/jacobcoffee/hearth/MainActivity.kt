package dev.jacobcoffee.hearth

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import dev.jacobcoffee.hearth.homekey.HomeRedirectService
import dev.jacobcoffee.hearth.theme.ResolvedTheme
import dev.jacobcoffee.hearth.theme.ThemeRepository
import dev.jacobcoffee.hearth.ui.HearthApp
import dev.jacobcoffee.hearth.ui.ReduceMotionStore
import dev.jacobcoffee.hearth.ui.theme.HearthTheme
import kotlinx.coroutines.delay
import java.time.Duration
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime

class MainActivity : ComponentActivity() {

    companion object {
        fun open(context: Context) {
            val intent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_CLEAR_TOP or
                    Intent.FLAG_ACTIVITY_SINGLE_TOP
            }
            context.startActivity(intent)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val themeRepository = ThemeRepository(applicationContext)
        setContent {
            var resolved by remember { mutableStateOf(themeRepository.resolveActive()) }
            var reduceMotion by remember { mutableStateOf(ReduceMotionStore.get(applicationContext)) }

            // Re-resolve the Automatic theme at midnight (device timezone) and whenever the
            // activity resumes, per the theme contract.
            LaunchedEffect(Unit) {
                while (true) {
                    val now = LocalDateTime.now()
                    val nextMidnight = LocalDate.now().plusDays(1).atStartOfDay()
                    delay(Duration.between(now, nextMidnight).toMillis().coerceAtLeast(1000L))
                    resolved = themeRepository.resolveActive()
                }
            }

            HearthTheme(resolved, reduceMotion) {
                HearthApp(
                    themeRepository = themeRepository,
                    reduceMotion = reduceMotion,
                    onReduceMotionChanged = { reduceMotion = it; ReduceMotionStore.set(applicationContext, it) },
                    onThemeChanged = { resolved = themeRepository.resolveActive() },
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        HomeRedirectService.setLiveActivity(this)
    }

    override fun onPause() {
        HomeRedirectService.clearLiveActivity(this)
        super.onPause()
    }
}
