package com.otterworks.report.util;

import org.junit.Test;

import java.util.Calendar;
import java.util.Date;
import java.util.TimeZone;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

/**
 * Boundary and negative cases for {@link ReportDateUtils} (WP-12).
 *
 * Every assertion uses a fixed epoch-millis instant so the suite does not depend
 * on the wall clock; the two "current time" helpers are asserted on their
 * invariants (all sub-day fields zeroed, day-of-month 1) rather than on a value.
 *
 * JUnit 4 to match the rest of this module.
 */
public class ReportDateUtilsTest {

    // 2024-03-01T12:34:56Z
    private static final long FIXED_MILLIS = 1709296496000L;
    private static final long ONE_DAY_MS = 86400000L;

    private static Date at(long millis) {
        return new Date(millis);
    }

    // ---- formatting: null handling ----

    @Test
    public void toIsoStringOfNullIsNull() {
        assertNull(ReportDateUtils.toIsoString(null));
    }

    @Test
    public void toDisplayStringOfNullIsNotAvailable() {
        assertEquals("N/A", ReportDateUtils.toDisplayString(null));
    }

    @Test
    public void toFileNameStringOfNullFallsBackToNow() {
        String name = ReportDateUtils.toFileNameString(null);
        assertNotNull(name);
        assertEquals(15, name.length()); // yyyyMMdd_HHmmss
    }

    @Test
    public void formattersUseUtcNotThePlatformZone() {
        assertEquals("2024-03-01T12:34:56Z", ReportDateUtils.toIsoString(at(FIXED_MILLIS)));
        assertEquals("Mar 01, 2024 12:34", ReportDateUtils.toDisplayString(at(FIXED_MILLIS)));
        assertEquals("20240301_123456", ReportDateUtils.toFileNameString(at(FIXED_MILLIS)));
    }

    @Test
    public void epochZeroFormatsAsTheUnixEpoch() {
        assertEquals("1970-01-01T00:00:00Z", ReportDateUtils.toIsoString(at(0L)));
    }

    // ---- parsing ----

    @Test
    public void parseIsoDateOfNullOrBlankIsNull() {
        assertNull(ReportDateUtils.parseIsoDate(null));
        assertNull(ReportDateUtils.parseIsoDate(""));
        assertNull(ReportDateUtils.parseIsoDate("   "));
    }

    @Test
    public void parseIsoDateAcceptsEverySupportedPattern() {
        assertNotNull(ReportDateUtils.parseIsoDate("2024-03-01T12:34:56Z"));
        assertNotNull(ReportDateUtils.parseIsoDate("2024-03-01T12:34:56+0000"));
        assertNotNull(ReportDateUtils.parseIsoDate("2024-03-01 12:34:56"));
        assertNotNull(ReportDateUtils.parseIsoDate("2024-03-01"));
    }

    @Test
    public void isoRoundTripPreservesTheInstant() {
        String iso = ReportDateUtils.toIsoString(at(FIXED_MILLIS));
        Date parsed = ReportDateUtils.parseIsoDate(iso);
        // Parsing "...Z" with a pattern whose Z is a literal yields the platform zone,
        // so compare the re-formatted value rather than the raw millis.
        assertEquals(iso, ReportDateUtils.toIsoString(shiftToUtc(parsed)));
    }

    private static Date shiftToUtc(Date parsedAsLocal) {
        int offset = TimeZone.getDefault().getOffset(parsedAsLocal.getTime());
        return new Date(parsedAsLocal.getTime() + offset);
    }

    @Test
    public void parseIsoDateRejectsAMalformedString() {
        try {
            ReportDateUtils.parseIsoDate("not-a-date");
            fail("expected IllegalArgumentException");
        } catch (IllegalArgumentException expected) {
            assertTrue(expected.getMessage().contains("not-a-date"));
        }
    }

    @Test
    public void parseIsoDateRejectsAnUnsupportedButPlausibleFormat() {
        try {
            ReportDateUtils.parseIsoDate("01/03/2024");
            fail("expected IllegalArgumentException");
        } catch (IllegalArgumentException expected) {
            assertNotNull(expected.getCause());
        }
    }

    @Test
    public void parseIsoDateRollsOverAnOutOfRangeCalendarDate() {
        // Commons Lang 2 uses a lenient SimpleDateFormat: 2024-02-31 silently
        // becomes 2024-03-02 instead of being rejected. Documented, not endorsed.
        Date parsed = ReportDateUtils.parseIsoDate("2024-02-31");
        assertNotNull(parsed);
        Calendar cal = Calendar.getInstance();
        cal.setTime(parsed);
        assertEquals(Calendar.MARCH, cal.get(Calendar.MONTH));
        assertEquals(2, cal.get(Calendar.DAY_OF_MONTH));
    }

    // ---- isWithinRange: inclusive on both ends ----

    @Test
    public void isWithinRangeIsInclusiveAtTheStartBoundary() {
        Date start = at(FIXED_MILLIS);
        Date end = at(FIXED_MILLIS + ONE_DAY_MS);

        assertFalse("start - 1ms is outside", ReportDateUtils.isWithinRange(at(FIXED_MILLIS - 1), start, end));
        assertTrue("start is inside", ReportDateUtils.isWithinRange(start, start, end));
        assertTrue("start + 1ms is inside", ReportDateUtils.isWithinRange(at(FIXED_MILLIS + 1), start, end));
    }

    @Test
    public void isWithinRangeIsInclusiveAtTheEndBoundary() {
        Date start = at(FIXED_MILLIS);
        Date end = at(FIXED_MILLIS + ONE_DAY_MS);

        assertTrue("end - 1ms is inside", ReportDateUtils.isWithinRange(at(end.getTime() - 1), start, end));
        assertTrue("end is inside", ReportDateUtils.isWithinRange(end, start, end));
        assertFalse("end + 1ms is outside", ReportDateUtils.isWithinRange(at(end.getTime() + 1), start, end));
    }

    @Test
    public void isWithinRangeAcceptsADegenerateSingleInstantRange() {
        Date only = at(FIXED_MILLIS);
        assertTrue(ReportDateUtils.isWithinRange(only, only, only));
    }

    @Test
    public void isWithinRangeRejectsAnInvertedRange() {
        Date start = at(FIXED_MILLIS + ONE_DAY_MS);
        Date end = at(FIXED_MILLIS);
        assertFalse(ReportDateUtils.isWithinRange(at(FIXED_MILLIS + 3600000L), start, end));
    }

    @Test
    public void isWithinRangeRejectsAnyNullArgument() {
        Date d = at(FIXED_MILLIS);
        assertFalse(ReportDateUtils.isWithinRange(null, d, d));
        assertFalse(ReportDateUtils.isWithinRange(d, null, d));
        assertFalse(ReportDateUtils.isWithinRange(d, d, null));
    }

    // ---- humanReadableDuration: unit roll-over boundaries ----

    @Test
    public void humanReadableDurationOfNullEndpointsIsUnknown() {
        Date d = at(FIXED_MILLIS);
        assertEquals("unknown", ReportDateUtils.humanReadableDuration(null, d));
        assertEquals("unknown", ReportDateUtils.humanReadableDuration(d, null));
        assertEquals("unknown", ReportDateUtils.humanReadableDuration(null, null));
    }

    @Test
    public void humanReadableDurationRollsOverFromSecondsToMinutes() {
        assertEquals("59s", durationOfSeconds(59));
        assertEquals("1m 0s", durationOfSeconds(60));
        assertEquals("1m 1s", durationOfSeconds(61));
    }

    @Test
    public void humanReadableDurationRollsOverFromMinutesToHours() {
        assertEquals("59m 59s", durationOfSeconds(3599));
        assertEquals("1h 0m", durationOfSeconds(3600));
        assertEquals("1h 0m", durationOfSeconds(3601));
    }

    @Test
    public void humanReadableDurationOfAZeroSpanIsZeroSeconds() {
        assertEquals("0s", durationOfSeconds(0));
    }

    @Test
    public void humanReadableDurationTruncatesSubSecondSpans() {
        Date start = at(FIXED_MILLIS);
        assertEquals("0s", ReportDateUtils.humanReadableDuration(start, at(FIXED_MILLIS + 999)));
    }

    @Test
    public void humanReadableDurationOfANegativeSpanIsNegativeSeconds() {
        // Negative case: end before start is not rejected, it produces "-5s".
        assertEquals("-5s", durationOfSeconds(-5));
    }

    private static String durationOfSeconds(long seconds) {
        Date start = at(FIXED_MILLIS);
        return ReportDateUtils.humanReadableDuration(start, at(FIXED_MILLIS + seconds * 1000L));
    }

    // ---- current-time helpers: invariants only ----

    @Test
    public void startOfTodayZeroesEverySubDayField() {
        Calendar cal = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        cal.setTime(ReportDateUtils.startOfToday());

        assertEquals(0, cal.get(Calendar.HOUR_OF_DAY));
        assertEquals(0, cal.get(Calendar.MINUTE));
        assertEquals(0, cal.get(Calendar.SECOND));
        assertEquals(0, cal.get(Calendar.MILLISECOND));
    }

    @Test
    public void startOfMonthIsTheFirstOfTheMonthAtMidnight() {
        Calendar cal = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        cal.setTime(ReportDateUtils.startOfMonth());

        assertEquals(1, cal.get(Calendar.DAY_OF_MONTH));
        assertEquals(0, cal.get(Calendar.HOUR_OF_DAY));
        assertEquals(0, cal.get(Calendar.MILLISECOND));
    }

    @Test
    public void startOfMonthIsNeverAfterStartOfToday() {
        assertFalse(ReportDateUtils.startOfMonth().after(ReportDateUtils.startOfToday()));
    }

    @Test
    public void daysAgoMovesBackwardsAndZeroDaysIsEssentiallyNow() {
        Date now = new Date();
        assertTrue(ReportDateUtils.daysAgo(1).before(now) || ReportDateUtils.daysAgo(1).equals(now));
        assertTrue(ReportDateUtils.daysAgo(30).before(ReportDateUtils.daysAgo(29)));
        // Negative input moves forward instead of being rejected.
        assertTrue(ReportDateUtils.daysAgo(-1).after(now));
    }
}
