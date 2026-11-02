package dev.jacobcoffee.hearth

import dev.jacobcoffee.hearth.media.extrapolatePosition
import dev.jacobcoffee.hearth.media.formatTrackTime
import org.junit.Assert.assertEquals
import org.junit.Test

class NowPlayingTest {
    @Test
    fun playingAdvancesFromLastReportAtItsSpeed() {
        assertEquals(13_000L, extrapolatePosition(10_000, 1_000, 1f, playing = true, durationMs = 200_000, nowElapsed = 4_000))
        assertEquals(16_000L, extrapolatePosition(10_000, 1_000, 2f, playing = true, durationMs = 200_000, nowElapsed = 4_000))
    }

    @Test
    fun pausedHoldsItsReportedPosition() {
        assertEquals(10_000L, extrapolatePosition(10_000, 1_000, 1f, playing = false, durationMs = 200_000, nowElapsed = 60_000))
    }

    @Test
    fun neverRunsPastTheEndOfAKnownTrack() {
        assertEquals(200_000L, extrapolatePosition(199_000, 0, 1f, playing = true, durationMs = 200_000, nowElapsed = 30_000))
    }

    @Test
    fun unknownLengthKeepsCounting() {
        assertEquals(30_500L, extrapolatePosition(500, 0, 1f, playing = true, durationMs = 0, nowElapsed = 30_000))
    }

    @Test
    fun formatsMinutesAndHours() {
        assertEquals("0:00", formatTrackTime(0))
        assertEquals("3:05", formatTrackTime(185_999))
        assertEquals("1:02:03", formatTrackTime(3_723_000))
    }
}
