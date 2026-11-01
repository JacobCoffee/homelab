package dev.jacobcoffee.hearth.theme

import java.time.MonthDay

/** A theme's id and season window, enough to resolve "Automatic". */
data class SeasonCandidate(
    val id: String,
    val from: String,
    val to: String,
    val priority: Int,
)

/**
 * Resolves Settings -> Theme "Automatic" to a concrete theme id.
 *
 * Among installed themes whose season window contains today (device timezone), pick the
 * one with the highest priority; ties go to the shorter window; remaining ties go to the
 * lexicographically first id. No match -> "default".
 */
object SeasonResolver {
    const val DEFAULT_THEME_ID = "default"

    fun resolve(candidates: List<SeasonCandidate>, today: MonthDay): String {
        val matching = candidates.filter { contains(parseMonthDay(it.from), parseMonthDay(it.to), today) }
        if (matching.isEmpty()) return DEFAULT_THEME_ID
        return matching
            .sortedWith(
                compareByDescending<SeasonCandidate> { it.priority }
                    .thenBy { windowLengthDays(parseMonthDay(it.from), parseMonthDay(it.to)) }
                    .thenBy { it.id }
            )
            .first()
            .id
    }

    /** Day-of-year ordinal on a fixed non-leap reference year, so Feb 29 never appears. */
    private fun ordinal(md: MonthDay): Int {
        val cumulative = intArrayOf(0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
        val day = if (md.monthValue == 2 && md.dayOfMonth == 29) 28 else md.dayOfMonth
        return cumulative[md.monthValue - 1] + day
    }

    fun contains(from: MonthDay, to: MonthDay, today: MonthDay): Boolean {
        val f = ordinal(from)
        val t = ordinal(to)
        val d = ordinal(today)
        return if (f <= t) d in f..t else d >= f || d <= t
    }

    fun windowLengthDays(from: MonthDay, to: MonthDay): Int {
        val f = ordinal(from)
        val t = ordinal(to)
        return if (f <= t) t - f + 1 else (365 - f) + t + 1
    }

    fun parseMonthDay(s: String): MonthDay {
        val parts = s.split("-")
        require(parts.size == 2) { "expected MM-DD, got $s" }
        val month = parts[0].toInt()
        val day = parts[1].toInt()
        return MonthDay.of(month, day)
    }
}
