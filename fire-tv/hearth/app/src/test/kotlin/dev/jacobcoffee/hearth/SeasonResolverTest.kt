package dev.jacobcoffee.hearth

import dev.jacobcoffee.hearth.theme.SeasonCandidate
import dev.jacobcoffee.hearth.theme.SeasonResolver
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.MonthDay

class SeasonResolverTest {

    @Test
    fun `no themes match falls back to default`() {
        val candidates = listOf(SeasonCandidate("christmas", "12-01", "12-26", 50))
        val result = SeasonResolver.resolve(candidates, MonthDay.of(3, 15))
        assertEquals("default", result)
    }

    @Test
    fun `simple non-wrapping window matches`() {
        val candidates = listOf(SeasonCandidate("summer", "06-01", "08-31", 10))
        assertEquals("summer", SeasonResolver.resolve(candidates, MonthDay.of(7, 4)))
        assertEquals("default", SeasonResolver.resolve(candidates, MonthDay.of(9, 1)))
    }

    @Test
    fun `window wrapping the year end matches both sides`() {
        val candidates = listOf(SeasonCandidate("winter", "12-15", "01-15", 20))
        assertEquals("winter", SeasonResolver.resolve(candidates, MonthDay.of(12, 31)))
        assertEquals("winter", SeasonResolver.resolve(candidates, MonthDay.of(1, 1)))
        assertEquals("winter", SeasonResolver.resolve(candidates, MonthDay.of(1, 15)))
        assertEquals("default", SeasonResolver.resolve(candidates, MonthDay.of(1, 16)))
        assertEquals("default", SeasonResolver.resolve(candidates, MonthDay.of(12, 14)))
    }

    @Test
    fun `higher priority wins among overlapping candidates`() {
        val candidates = listOf(
            SeasonCandidate("autumn", "09-01", "11-30", 10),
            SeasonCandidate("thanksgiving", "11-20", "11-28", 60),
        )
        assertEquals("thanksgiving", SeasonResolver.resolve(candidates, MonthDay.of(11, 25)))
        assertEquals("autumn", SeasonResolver.resolve(candidates, MonthDay.of(11, 15)))
    }

    @Test
    fun `equal priority ties broken by shorter window`() {
        val candidates = listOf(
            SeasonCandidate("long-window", "11-01", "11-30", 50),
            SeasonCandidate("short-window", "11-20", "11-25", 50),
        )
        assertEquals("short-window", SeasonResolver.resolve(candidates, MonthDay.of(11, 22)))
    }

    @Test
    fun `equal priority and length ties broken by id order`() {
        val candidates = listOf(
            SeasonCandidate("zeta", "11-20", "11-25", 50),
            SeasonCandidate("alpha", "11-20", "11-25", 50),
        )
        assertEquals("alpha", SeasonResolver.resolve(candidates, MonthDay.of(11, 22)))
    }

    @Test
    fun `single day window matches only that day`() {
        val candidates = listOf(SeasonCandidate("new-year", "01-01", "01-01", 100))
        assertEquals("new-year", SeasonResolver.resolve(candidates, MonthDay.of(1, 1)))
        assertEquals("default", SeasonResolver.resolve(candidates, MonthDay.of(1, 2)))
        assertEquals("default", SeasonResolver.resolve(candidates, MonthDay.of(12, 31)))
    }

    @Test
    fun `window length counts wrap correctly for tie break`() {
        // winterWrap spans Dec 20 - Jan 5 (17 days), nonWrap spans Dec 1 - Dec 31 (31 days):
        // same priority, Dec 25 falls in both, the shorter (wrap) window should win.
        val candidates = listOf(
            SeasonCandidate("whole-december", "12-01", "12-31", 30),
            SeasonCandidate("holiday-wrap", "12-20", "01-05", 30),
        )
        assertEquals("holiday-wrap", SeasonResolver.resolve(candidates, MonthDay.of(12, 25)))
    }
}
