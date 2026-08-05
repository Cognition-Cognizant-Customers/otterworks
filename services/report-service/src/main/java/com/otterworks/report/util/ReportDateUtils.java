package com.otterworks.report.util;

import org.apache.commons.lang3.StringUtils;
import org.apache.commons.lang3.time.DateFormatUtils;
import org.apache.commons.lang3.time.DateUtils;

import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.TimeZone;

/**
 * Date utility class using legacy java.util.Date and Commons Lang 3.
 *
 * LEGACY PATTERNS (multiple upgrade targets):
 * 1. java.util.Date everywhere → java.time.Instant / LocalDateTime / ZonedDateTime
 * 2. SimpleDateFormat (not thread-safe) → DateTimeFormatter (thread-safe)
 * 3. Commons Lang DateUtils/DateFormatUtils → java.time API (no external dep needed)
 * 4. Calendar manipulation → java.time.temporal.ChronoUnit / Period
 * 5. Manual timezone handling → ZoneId / ZoneOffset
 *
 * This class is a prime candidate for complete rewrite during Java 8→17 migration.
 */
public final class ReportDateUtils {

    // LEGACY: SimpleDateFormat is NOT thread-safe — shared instance is a bug
    // in multithreaded environments. java.time.DateTimeFormatter is immutable and thread-safe.
    private static final SimpleDateFormat ISO_FORMAT = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'");
    private static final SimpleDateFormat DISPLAY_FORMAT = new SimpleDateFormat("MMM dd, yyyy HH:mm");
    private static final SimpleDateFormat FILE_NAME_FORMAT = new SimpleDateFormat("yyyyMMdd_HHmmss");

    static {
        ISO_FORMAT.setTimeZone(TimeZone.getTimeZone("UTC"));
        DISPLAY_FORMAT.setTimeZone(TimeZone.getTimeZone("UTC"));
        FILE_NAME_FORMAT.setTimeZone(TimeZone.getTimeZone("UTC"));
    }

    private ReportDateUtils() {
        // Utility class
    }

    /**
     * Format a Date to ISO 8601 string.
     * LEGACY: Uses SimpleDateFormat (not thread-safe).
     */
    public static String toIsoString(Date date) {
        if (date == null) {
            return null;
        }
        return DateFormatUtils.formatUTC(date, "yyyy-MM-dd'T'HH:mm:ss'Z'");
    }

    /**
     * Format a Date for display purposes.
     */
    public static String toDisplayString(Date date) {
        if (date == null) {
            return "N/A";
        }
        synchronized (DISPLAY_FORMAT) {
            return DISPLAY_FORMAT.format(date);
        }
    }

    /**
     * Format a Date for use in file names.
     */
    public static String toFileNameString(Date date) {
        if (date == null) {
            date = new Date();
        }
        synchronized (FILE_NAME_FORMAT) {
            return FILE_NAME_FORMAT.format(date);
        }
    }

    /**
     * Parse an ISO 8601 date string.
     * LEGACY: Uses SimpleDateFormat parsing (brittle, not thread-safe).
     */
    public static Date parseIsoDate(String dateString) {
        if (StringUtils.isBlank(dateString)) {
            return null;
        }
        try {
            return DateUtils.parseDate(dateString, new String[]{
                    "yyyy-MM-dd'T'HH:mm:ss'Z'",
                    "yyyy-MM-dd'T'HH:mm:ssZ",
                    "yyyy-MM-dd HH:mm:ss",
                    "yyyy-MM-dd"
            });
        } catch (ParseException e) {
            throw new IllegalArgumentException("Cannot parse date: " + dateString, e);
        }
    }

    /**
     * Get the start of today (midnight UTC).
     * LEGACY: Calendar manipulation instead of LocalDate.atStartOfDay().
     */
    public static Date startOfToday() {
        Calendar cal = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        cal.set(Calendar.HOUR_OF_DAY, 0);
        cal.set(Calendar.MINUTE, 0);
        cal.set(Calendar.SECOND, 0);
        cal.set(Calendar.MILLISECOND, 0);
        return cal.getTime();
    }

    /**
     * Get the start of this month.
     * LEGACY: Calendar manipulation instead of YearMonth.
     */
    public static Date startOfMonth() {
        Calendar cal = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        cal.set(Calendar.DAY_OF_MONTH, 1);
        cal.set(Calendar.HOUR_OF_DAY, 0);
        cal.set(Calendar.MINUTE, 0);
        cal.set(Calendar.SECOND, 0);
        cal.set(Calendar.MILLISECOND, 0);
        return cal.getTime();
    }

    /**
     * Subtract days from a date.
     * LEGACY: Commons Lang DateUtils.addDays with negative value.
     */
    public static Date daysAgo(int days) {
        return DateUtils.addDays(new Date(), -days);
    }

    /**
     * Check if a date falls within a range (inclusive).
     */
    public static boolean isWithinRange(Date date, Date start, Date end) {
        if (date == null || start == null || end == null) {
            return false;
        }
        return !date.before(start) && !date.after(end);
    }

    /**
     * Calculate duration between two dates in a human-readable format.
     * LEGACY: Manual millisecond arithmetic instead of Duration.between().
     */
    public static String humanReadableDuration(Date start, Date end) {
        if (start == null || end == null) {
            return "unknown";
        }
        long diffMs = end.getTime() - start.getTime();
        long seconds = diffMs / 1000;
        long minutes = seconds / 60;
        long hours = minutes / 60;

        if (hours > 0) {
            return hours + "h " + (minutes % 60) + "m";
        } else if (minutes > 0) {
            return minutes + "m " + (seconds % 60) + "s";
        } else {
            return seconds + "s";
        }
    }
}
