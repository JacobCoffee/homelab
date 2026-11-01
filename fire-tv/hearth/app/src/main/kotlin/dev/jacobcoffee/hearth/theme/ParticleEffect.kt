package dev.jacobcoffee.hearth.theme

import android.graphics.Bitmap
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import dev.jacobcoffee.hearth.ui.theme.LocalAmbientClock
import kotlin.math.cos
import kotlin.math.sin
import kotlin.random.Random

/** Hard cap from the performance budget: idle CPU stays low even on dense themes. */
private const val MAX_PARTICLES = 60
private const val MIN_PARTICLES = 8

/** Global drift speed: ambient, not weather. */
private const val SPEED = 0.4f

/** Fresh particles fade in over this long; particles also fade out near the screen edges. */
private const val FADE_IN_MS = 1500f
private const val EDGE_FADE = 0.15f

/** Soft sprites are drawn this many times the particle's core size so the falloff has room. */
private const val GLOW_SCALE = 5f

private class Particle(
    var x: Float,
    var y: Float,
    var vx: Float,
    var vy: Float,
    var size: Float,
    var phase: Float,
    var rotation: Float = 0f,
    var bornMs: Long = 0L,
)

/** Simulation state that lives across draw passes; mutated only inside the Canvas draw. */
private class FieldState {
    val particles = mutableListOf<Particle>()
    val bursts = mutableListOf<FireworkBurst>()
    var lastNanos = 0L
}

/**
 * Draws a theme's particle effect, stepping it on [LocalAmbientClock]. Nothing is drawn when
 * the effect is "none" or the clock is off (Reduce motion, or the activity is not resumed).
 */
@Composable
fun ParticleField(effect: ThemeEffect?, modifier: Modifier = Modifier) {
    val type = effect?.particleType() ?: ParticleType.none
    val clock = LocalAmbientClock.current
    if (effect == null || type == ParticleType.none || clock == null) return

    val density = effect.density.coerceIn(0.0, 1.0).toFloat()
    val overrideColor = effect.color?.let { hex ->
        runCatching { Color(android.graphics.Color.parseColor(hex)) }.getOrNull()
    }
    val screenDensity = LocalContext.current.resources.displayMetrics.density
    val field = remember(type, density) { FieldState() }
    val sprite = remember { softDotSprite() }
    val particles = field.particles
    val bursts = field.bursts

    Canvas(modifier = modifier.fillMaxSize().graphicsLayer()) {
        // Reading the clock here invalidates only this layer's draw on each tick.
        val now = clock.value
        val dt = if (field.lastNanos == 0L) 0f else ((now - field.lastNanos) / 1_000_000_000f).coerceIn(0f, 0.1f)
        field.lastNanos = now
        val nowMs = now / 1_000_000L
        stepSimulation(type, density, screenDensity, particles, bursts, nowMs, dt)
        when (type) {
            ParticleType.fireworks -> drawFireworks(bursts, overrideColor)
            ParticleType.hearts -> drawGlyphParticles(particles, "\u2764", overrideColor ?: Color(0xFFE05570), nowMs)
            ParticleType.stars -> drawGlyphParticles(particles, "\u2726", overrideColor ?: Color(0xFFF4E3A1), nowMs)
            ParticleType.bats -> drawGlyphParticles(particles, "\uD83E\uDD87", overrideColor ?: Color(0xFF1A1A1A), nowMs)
            ParticleType.snow -> drawSoftParticles(particles, sprite, overrideColor ?: Color.White, nowMs, twinkle = false)
            ParticleType.embers -> drawSoftParticles(particles, sprite, overrideColor ?: Color(0xFFFF6A3D), nowMs, twinkle = true)
            ParticleType.leaves -> drawLeafParticles(particles, overrideColor ?: Color(0xFFC97A2B), nowMs)
            ParticleType.fireflies -> drawSoftParticles(particles, sprite, overrideColor ?: Color(0xFFD9F27A), nowMs, twinkle = true)
            ParticleType.confetti -> drawConfettiParticles(particles, overrideColor, nowMs)
            ParticleType.none -> Unit
        }
    }
}

private fun stepSimulation(
    type: ParticleType,
    density: Float,
    screenDensity: Float,
    particles: MutableList<Particle>,
    bursts: MutableList<FireworkBurst>,
    nowMs: Long,
    dt: Float,
) {
    val target = (density * MAX_PARTICLES).toInt().coerceIn(if (type == ParticleType.fireworks) 0 else MIN_PARTICLES, MAX_PARTICLES)

    if (type == ParticleType.fireworks) {
        bursts.removeAll { nowMs - it.spawnedMs > 1400L }
        val intervalMs = (2600L - (density * 1800L).toLong()).coerceAtLeast(600L)
        if (bursts.isEmpty() || nowMs - bursts.last().spawnedMs > intervalMs) {
            bursts += newBurst(nowMs)
        }
        for (b in bursts) b.advance(dt)
        return
    }

    while (particles.size < target) particles += spawnParticle(type, screenDensity, respawn = true).also { it.bornMs = nowMs }
    while (particles.size > target) particles.removeAt(particles.size - 1)

    val drift = dt * SPEED
    for (p in particles) {
        advanceParticle(type, p, drift)
        if (p.y > 1.05f || p.y < -0.05f || p.x > 1.05f || p.x < -0.05f) {
            val fresh = spawnParticle(type, screenDensity, respawn = false)
            p.x = fresh.x; p.y = fresh.y; p.vx = fresh.vx; p.vy = fresh.vy
            p.size = fresh.size; p.phase = fresh.phase; p.bornMs = nowMs
        }
    }
}

private fun spawnParticle(type: ParticleType, screenDensity: Float, respawn: Boolean): Particle {
    val x = Random.nextFloat()
    return when (type) {
        ParticleType.snow -> Particle(x, if (respawn) Random.nextFloat() else -0.05f, Random.nextFloat() * 0.04f - 0.02f, 0.05f + Random.nextFloat() * 0.08f, (2f + Random.nextFloat() * 3f) * screenDensity, Random.nextFloat() * 6.28f)
        ParticleType.embers -> Particle(x, if (respawn) Random.nextFloat() else 1.05f, Random.nextFloat() * 0.03f - 0.015f, -(0.06f + Random.nextFloat() * 0.1f), (1.5f + Random.nextFloat() * 2.5f) * screenDensity, Random.nextFloat() * 6.28f)
        ParticleType.leaves -> Particle(x, if (respawn) Random.nextFloat() else -0.05f, Random.nextFloat() * 0.06f - 0.03f, 0.04f + Random.nextFloat() * 0.06f, (4f + Random.nextFloat() * 4f) * screenDensity, Random.nextFloat() * 6.28f)
        ParticleType.fireflies -> Particle(x, Random.nextFloat(), Random.nextFloat() * 0.02f - 0.01f, Random.nextFloat() * 0.02f - 0.01f, (2f + Random.nextFloat() * 2f) * screenDensity, Random.nextFloat() * 6.28f)
        ParticleType.confetti -> Particle(x, if (respawn) Random.nextFloat() else -0.05f, Random.nextFloat() * 0.03f - 0.015f, 0.08f + Random.nextFloat() * 0.08f, (3f + Random.nextFloat() * 3f) * screenDensity, Random.nextFloat() * 6.28f)
        ParticleType.hearts -> Particle(x, if (respawn) Random.nextFloat() else 1.05f, Random.nextFloat() * 0.02f - 0.01f, -(0.04f + Random.nextFloat() * 0.05f), (10f + Random.nextFloat() * 8f) * screenDensity, Random.nextFloat() * 6.28f)
        ParticleType.stars -> Particle(x, Random.nextFloat(), 0f, 0f, (8f + Random.nextFloat() * 6f) * screenDensity, Random.nextFloat() * 6.28f)
        ParticleType.bats -> Particle(if (respawn) x else -0.05f, Random.nextFloat() * 0.5f, 0.05f + Random.nextFloat() * 0.05f, 0f, (16f + Random.nextFloat() * 10f) * screenDensity, Random.nextFloat() * 6.28f)
        ParticleType.none, ParticleType.fireworks -> Particle(x, 0f, 0f, 0f, 0f, 0f)
    }
}

private fun advanceParticle(type: ParticleType, p: Particle, dt: Float) {
    p.phase += dt
    when (type) {
        ParticleType.snow -> { p.x += (sin(p.phase * 1.5f) * 0.1f) * dt; p.y += p.vy * dt }
        ParticleType.embers -> { p.x += p.vx * dt; p.y += p.vy * dt }
        ParticleType.leaves -> { p.x += (sin(p.phase * 2f) * 0.15f) * dt; p.y += p.vy * dt; p.rotation += dt * 1.5f }
        ParticleType.fireflies -> {
            p.x += p.vx * dt + sin(p.phase) * 0.0015f
            p.y += p.vy * dt + cos(p.phase * 0.8f) * 0.0015f
            p.x = p.x.coerceIn(0f, 1f); p.y = p.y.coerceIn(0f, 1f)
        }
        ParticleType.confetti -> { p.x += (sin(p.phase * 3f) * 0.08f) * dt; p.y += p.vy * dt; p.rotation += dt * 3f }
        ParticleType.hearts -> { p.x += (sin(p.phase) * 0.06f) * dt; p.y += p.vy * dt }
        ParticleType.stars -> { /* twinkle in place, alpha handled at draw time via phase */ }
        ParticleType.bats -> { p.x += p.vx * dt; p.y += sin(p.phase * 4f) * 0.002f }
        ParticleType.none, ParticleType.fireworks -> Unit
    }
}

/** A white radial falloff, tinted per theme at draw time: soft edges without a blur pass. */
private fun softDotSprite(): ImageBitmap {
    val px = 64
    val bitmap = Bitmap.createBitmap(px, px, Bitmap.Config.ARGB_8888)
    val r = px / 2f
    val paint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
        shader = RadialGradient(
            r, r, r,
            intArrayOf(0xFFFFFFFF.toInt(), 0x99FFFFFF.toInt(), 0x33FFFFFF, 0x00FFFFFF),
            floatArrayOf(0f, 0.18f, 0.5f, 1f),
            Shader.TileMode.CLAMP,
        )
    }
    android.graphics.Canvas(bitmap).drawCircle(r, r, r, paint)
    return bitmap.asImageBitmap()
}

/** 0..1: fades a particle in after it spawns and out as it nears any screen edge. */
private fun fade(p: Particle, nowMs: Long): Float {
    val age = if (p.bornMs == 0L) FADE_IN_MS else (nowMs - p.bornMs).toFloat()
    val fadeIn = (age / FADE_IN_MS).coerceIn(0f, 1f)
    val edge = minOf(p.x, 1f - p.x, p.y, 1f - p.y)
    val fadeEdge = (edge / EDGE_FADE).coerceIn(0f, 1f)
    return fadeIn * fadeEdge
}

private fun DrawScope.drawSoftParticles(particles: List<Particle>, sprite: ImageBitmap, color: Color, nowMs: Long, twinkle: Boolean) {
    val tint = ColorFilter.tint(color)
    val src = IntSize(sprite.width, sprite.height)
    for (p in particles) {
        val pulse = if (twinkle) 0.45f + 0.55f * ((sin(p.phase * 1.2f) + 1f) / 2f) else 0.8f
        val alpha = (pulse * fade(p, nowMs)).coerceIn(0f, 1f)
        if (alpha <= 0.01f) continue
        val d = (p.size * GLOW_SCALE).toInt().coerceAtLeast(4)
        drawImage(
            sprite,
            srcSize = src,
            dstOffset = IntOffset((p.x * size.width).toInt() - d / 2, (p.y * size.height).toInt() - d / 2),
            dstSize = IntSize(d, d),
            alpha = alpha,
            colorFilter = tint,
        )
    }
}

private fun DrawScope.drawLeafParticles(particles: List<Particle>, color: Color, nowMs: Long) {
    for (p in particles) {
        rotate(p.rotation * 57.3f, pivot = androidx.compose.ui.geometry.Offset(p.x * size.width, p.y * size.height)) {
            drawOval(
                color.copy(alpha = 0.6f * fade(p, nowMs)),
                topLeft = androidx.compose.ui.geometry.Offset(p.x * size.width - p.size / 2, p.y * size.height - p.size / 4),
                size = androidx.compose.ui.geometry.Size(p.size, p.size / 2),
            )
        }
    }
}

private val confettiPalette = listOf(Color(0xFFE05570), Color(0xFF55A6E0), Color(0xFFF2C14E), Color(0xFF6FCF97))

private fun DrawScope.drawConfettiParticles(particles: List<Particle>, overrideColor: Color?, nowMs: Long) {
    particles.forEachIndexed { i, p ->
        val color = overrideColor ?: confettiPalette[i % confettiPalette.size]
        rotate(p.rotation * 57.3f, pivot = androidx.compose.ui.geometry.Offset(p.x * size.width, p.y * size.height)) {
            drawRect(
                color.copy(alpha = 0.65f * fade(p, nowMs)),
                topLeft = androidx.compose.ui.geometry.Offset(p.x * size.width - p.size / 2, p.y * size.height - p.size / 2),
                size = androidx.compose.ui.geometry.Size(p.size, p.size),
            )
        }
    }
}

private fun DrawScope.drawGlyphParticles(particles: List<Particle>, glyph: String, color: Color, nowMs: Long) {
    val paint = Paint().apply {
        isAntiAlias = true
        textAlign = Paint.Align.CENTER
    }
    drawIntoCanvas { canvas ->
        for (p in particles) {
            val base = if (p.vx == 0f && p.vy == 0f) (0.25f + 0.5f * ((sin(p.phase * 1.2f) + 1f) / 2f)) else 0.7f
            paint.color = color.copy(alpha = (base * fade(p, nowMs)).coerceIn(0f, 1f)).toArgb()
            paint.textSize = p.size
            canvas.nativeCanvas.drawText(glyph, p.x * size.width, p.y * size.height, paint)
        }
    }
}

private class FireworkBurst(val cx: Float, val cy: Float, val color: Color, val spawnedMs: Long) {
    val rays = List(14) { i ->
        val angle = (i / 14f) * 6.2832f
        floatArrayOf(cos(angle), sin(angle))
    }
    var progress = 0f
    fun advance(dt: Float) {
        progress = (progress + dt / 1.2f).coerceAtMost(1f)
    }
}

private val fireworkPalette = listOf(Color(0xFFE05570), Color(0xFF55A6E0), Color(0xFFF2C14E), Color(0xFF6FCF97), Color(0xFFBD6FE0))

private fun newBurst(nowMs: Long): FireworkBurst = FireworkBurst(
    cx = 0.15f + Random.nextFloat() * 0.7f,
    cy = 0.15f + Random.nextFloat() * 0.4f,
    color = fireworkPalette.random(),
    spawnedMs = nowMs,
)

private fun DrawScope.drawFireworks(bursts: List<FireworkBurst>, overrideColor: Color?) {
    for (b in bursts) {
        val alpha = (1f - b.progress).coerceIn(0f, 1f)
        val radius = b.progress * size.minDimension * 0.18f
        val color = (overrideColor ?: b.color).copy(alpha = alpha)
        for (ray in b.rays) {
            val px = (b.cx * size.width) + ray[0] * radius
            val py = (b.cy * size.height) + ray[1] * radius
            drawCircle(color, radius = 3.5f, center = androidx.compose.ui.geometry.Offset(px, py))
        }
    }
}
