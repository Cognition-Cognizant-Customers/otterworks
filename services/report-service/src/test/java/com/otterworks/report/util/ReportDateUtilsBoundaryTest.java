package com.otterworks.report.util;

import org.junit.Ignore;
import org.junit.Test;

import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

/**
 * Range, parsing and formatting boundaries of {@link ReportDateUtils}.
 *
 * All fixed instants are epoch-millis literals and every formatting expectation is
 * UTC, because the formatting helpers format in UTC; the one locale-sensitive stamp
 * ({@code toDisplayString}) is compared against a formatter built with the runner's own
 * FORMAT locale rather than an English literal. Parsing is the exception:
 * {@code parseIsoDate} resolves text in the JVM's default zone (see
 * {@link #aTimestampIsParsedInTheJvmZoneRatherThanAsUtc()}), so parse expectations
 * are built from a default-zone {@link Calendar} instead of a hard-coded UTC string,
 * which keeps them true on a runner in any zone. The three "now"-based helpers are
 * asserted structurally (field values, ordering) rather than against a wall-clock value.
 */
public class ReportDateUtilsBoundaryTest {

    /** 2023-11-14T22:13:20Z. */
    private static final long START_MS = 1_700_000_000_000L;
    private static final long END_MS = START_MS + 86_400_000L;

    private static final Date START = new Date(START_MS);
    private static final Date END = new Date(END_MS);

    // ------------------------------------------------------------ isWithinRange

    @Test
    public void oneMillisecondBeforeTheStartIsOutsideTheRange() {
        assertFalse(ReportDateUtils.isWithinRange(new Date(START_MS - 1), START, END));
    }

    @Test
    public void exactlyTheStartIsInsideTheRange() {
        assertTrue(ReportDateUtils.isWithinRange(new Date(START_MS), START, END));
    }

    @Test
    public void oneMillisecondAfterTheStartIsInsideTheRange() {
        assertTrue(ReportDateUtils.isWithinRange(new Date(START_MS + 1), START, END));
    }

    @Test
    public void oneMillisecondBeforeTheEndIsInsideTheRange() {
        assertTrue(ReportDateUtils.isWithinRange(new Date(END_MS - 1), START, END));
    }

    @Test
    public void exactlyTheEndIsInsideTheRange() {
        assertTrue(ReportDateUtils.isWithinRange(new Date(END_MS), START, END));
    }

    @Test
    public void oneMillisecondAfterTheEndIsOutsideTheRange() {
        assertFalse(ReportDateUtils.isWithinRange(new Date(END_MS + 1), START, END));
    }

    @Test
    public void aZeroWidthRangeContainsOnlyItsOwnInstant() {
        assertTrue(ReportDateUtils.isWithinRange(START, START, START));
        assertFalse(ReportDateUtils.isWithinRange(new Date(START_MS + 1), START, START));
    }

    @Test
    public void anInvertedRangeContainsNothing() {
        assertFalse(ReportDateUtils.isWithinRange(new Date(START_MS + 1000), END, START));
        assertFalse(ReportDateUtils.isWithinRange(START, END, START));
        assertFalse(ReportDateUtils.isWithinRange(END, END, START));
    }

    @Test
    public void aNullArgumentPutsTheDateOutsideTheRange() {
        assertFalse(ReportDateUtils.isWithinRange(null, START, END));
        assertFalse(ReportDateUtils.isWithinRange(START, null, END));
        assertFalse(ReportDateUtils.isWithinRange(START, START, null));
    }

    // -------------------------------------------------------------- parseIsoDate

    @Test
    public void everyDocumentedInputFormatParses() {
        assertNotNull(ReportDateUtils.parseIsoDate("2024-03-01T10:15:30Z"));
        assertNotNull(ReportDateUtils.parseIsoDate("2024-03-01T10:15:30+0000"));
        assertNotNull(ReportDateUtils.parseIsoDate("2024-03-01 10:15:30"));
        assertNotNull(ReportDateUtils.parseIsoDate("2024-03-01"));
    }

    @Test
    public void aTimestampIsParsedInTheJvmZoneRatherThanAsUtc() {
        // FINDING (documented, not fixed here): the "Z" in the parse patterns is a literal,
        // and Commons Lang 2 DateUtils.parseDate builds a SimpleDateFormat on the JVM default
        // zone, so "...T10:15:30Z" is read as 10:15:30 *local* time. toIsoString then formats
        // in UTC, so parse -> format only round-trips on a UTC JVM; elsewhere the instant is
        // shifted by the local offset.
        Date parsed = ReportDateUtils.parseIsoDate("2024-03-01T10:15:30Z");

        assertEquals(localTime(2024, 3, 1, 10, 15, 30), parsed);
    }

    @Test
    @Ignore("FINDING: parseIsoDate ignores the UTC designator, so an ISO instant does not round-trip off a UTC JVM")
    public void aParsedIsoInstantShouldRoundTripBackToTheSameString() {
        Date parsed = ReportDateUtils.parseIsoDate("2024-03-01T10:15:30Z");

        assertEquals("2024-03-01T10:15:30Z", ReportDateUtils.toIsoString(parsed));
    }

    @Test
    public void nullAndBlankInputParseToNull() {
        assertNull(ReportDateUtils.parseIsoDate(null));
        assertNull(ReportDateUtils.parseIsoDate(""));
        assertNull(ReportDateUtils.parseIsoDate("   "));
    }

    @Test
    public void anUnparseableStringIsRejected() {
        assertRejected("not-a-date");
        assertRejected("01/03/2024");
        assertRejected("2024-03-01T10:15:30.123456Z");
    }

    @Test
    public void anOutOfRangeCalendarDateRollsOverRatherThanFailing() {
        // Commons Lang 2 DateUtils.parseDate is lenient: 2024-02-30 becomes 2024-03-01.
        assertEquals(localTime(2024, 3, 1, 0, 0, 0), ReportDateUtils.parseIsoDate("2024-02-30"));
    }

    @Test
    public void aLeapDayParsesAsItself() {
        assertEquals(localTime(2024, 2, 29, 0, 0, 0), ReportDateUtils.parseIsoDate("2024-02-29"));
    }

    // -------------------------------------------------------------- formatting

    @Test
    public void formattingNullProducesTheDocumentedPlaceholders() {
        assertNull(ReportDateUtils.toIsoString(null));
        assertEquals("N/A", ReportDateUtils.toDisplayString(null));
        assertNotNull(ReportDateUtils.toFileNameString(null));
    }

    @Test
    public void theEpochFormatsAsUtcMidnight() {
        assertEquals("1970-01-01T00:00:00Z", ReportDateUtils.toIsoString(new Date(0L)));
    }

    @Test
    public void isoFormattingIsUtcRegardlessOfTheRunnersZone() {
        // 2024-07-01T00:30:00Z is still 2024-06-30 in New York — the UTC value must win.
        assertEquals("2024-07-01T00:30:00Z", ReportDateUtils.toIsoString(new Date(1_719_793_800_000L)));
    }

    @Test
    public void fileNameFormattingProducesACompactUtcStamp() {
        String stamp = ReportDateUtils.toFileNameString(new Date(1_719_793_800_000L));
        assertEquals("20240701_003000", stamp);
    }

    @Test
    public void displayFormattingIsUtc() {
        // DISPLAY_FORMAT pins its zone to UTC but not its locale, so the "MMM" month
        // abbreviation is rendered in the runner's FORMAT locale ("Jul" on an English JVM,
        // "juil." on a French one). The expectation is therefore built with that same locale
        // and the locale-free remainder of the stamp is asserted separately.
        Date instant = new Date(1_719_793_800_000L);
        SimpleDateFormat utcInRunnerLocale =
                new SimpleDateFormat("MMM dd, yyyy HH:mm", Locale.getDefault(Locale.Category.FORMAT));
        utcInRunnerLocale.setTimeZone(TimeZone.getTimeZone("UTC"));

        String displayed = ReportDateUtils.toDisplayString(instant);

        assertEquals(utcInRunnerLocale.format(instant), displayed);
        assertTrue(displayed, displayed.endsWith("01, 2024 00:30"));
    }

    // ------------------------------------------------------- duration arithmetic

    @Test
    public void aNullEndpointYieldsAnUnknownDuration() {
        assertEquals("unknown", ReportDateUtils.humanReadableDuration(null, END));
        assertEquals("unknown", ReportDateUtils.humanReadableDuration(START, null));
        assertEquals("unknown", ReportDateUtils.humanReadableDuration(null, null));
    }

    @Test
    public void aZeroDurationReadsAsZeroSeconds() {
        assertEquals("0s", ReportDateUtils.humanReadableDuration(START, START));
    }

    @Test
    public void subSecondDurationsTruncateDownToZeroSeconds() {
        assertEquals("0s", ReportDateUtils.humanReadableDuration(START, new Date(START_MS + 999)));
    }

    @Test
    public void theMinuteBoundaryTrioReadsCorrectly() {
        assertEquals("59s", ReportDateUtils.humanReadableDuration(START, new Date(START_MS + 59_000)));
        assertEquals("1m 0s", ReportDateUtils.humanReadableDuration(START, new Date(START_MS + 60_000)));
        assertEquals("1m 1s", ReportDateUtils.humanReadableDuration(START, new Date(START_MS + 61_000)));
    }

    @Test
    public void theHourBoundaryTrioReadsCorrectly() {
        assertEquals("59m 59s", ReportDateUtils.humanReadableDuration(START, new Date(START_MS + 3_599_000)));
        assertEquals("1h 0m", ReportDateUtils.humanReadableDuration(START, new Date(START_MS + 3_600_000)));
        assertEquals("1h 0m", ReportDateUtils.humanReadableDuration(START, new Date(START_MS + 3_660_000 - 60_000 + 1)));
    }

    @Test
    public void aNegativeDurationIsRenderedAsNegativeSecondsRatherThanClamped() {
        // Documented behaviour: the helper does no ordering check, so an end before the
        // start produces a negative reading instead of "unknown" or 0s.
        assertEquals("-30s", ReportDateUtils.humanReadableDuration(new Date(START_MS + 30_000), START));
    }

    @Test
    public void aMultiHourDurationReportsTheRemainderInMinutes() {
        assertEquals("25h 1m", ReportDateUtils.humanReadableDuration(START, new Date(START_MS + 90_060_000L)));
    }

    // ------------------------------------------------------------ relative dates

    @Test
    public void daysAgoZeroIsNotInTheFuture() {
        assertTrue(ReportDateUtils.daysAgo(0).getTime() <= System.currentTimeMillis() + 1000L);
    }

    @Test
    public void daysAgoIsMonotonicInItsArgument() {
        assertTrue(ReportDateUtils.daysAgo(30).before(ReportDateUtils.daysAgo(7)));
        assertTrue(ReportDateUtils.daysAgo(7).before(ReportDateUtils.daysAgo(0)));
    }

    @Test
    public void aNegativeDaysAgoMovesForwardInTime() {
        assertTrue(ReportDateUtils.daysAgo(-1).after(new Date()));
    }

    @Test
    public void startOfTodayIsUtcMidnight() {
        Calendar cal = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        cal.setTime(ReportDateUtils.startOfToday());

        assertEquals(0, cal.get(Calendar.HOUR_OF_DAY));
        assertEquals(0, cal.get(Calendar.MINUTE));
        assertEquals(0, cal.get(Calendar.SECOND));
        assertEquals(0, cal.get(Calendar.MILLISECOND));
    }

    @Test
    public void startOfMonthIsTheFirstDayAtUtcMidnight() {
        Calendar cal = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        cal.setTime(ReportDateUtils.startOfMonth());

        assertEquals(1, cal.get(Calendar.DAY_OF_MONTH));
        assertEquals(0, cal.get(Calendar.HOUR_OF_DAY));
        assertEquals(0, cal.get(Calendar.MILLISECOND));
    }

    @Test
    public void startOfMonthNeverFollowsStartOfToday() {
        assertFalse(ReportDateUtils.startOfMonth().after(ReportDateUtils.startOfToday()));
    }

    /** The instant a date-time string denotes when read in the JVM's own zone. */
    private static Date localTime(int year, int month, int day, int hour, int minute, int second) {
        Calendar cal = Calendar.getInstance();
        cal.clear();
        cal.set(year, month - 1, day, hour, minute, second);
        return cal.getTime();
    }

    private static void assertRejected(String input) {
        try {
            ReportDateUtils.parseIsoDate(input);
            fail("Expected IllegalArgumentException for input: " + input);
        } catch (IllegalArgumentException expected) {
            assertTrue(expected.getMessage().contains(input));
        }
    }
}
