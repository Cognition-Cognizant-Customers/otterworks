import { describe, it, expect, afterEach, vi } from "vitest";
import {
  cn,
  formatFileSize,
  formatRelativeTime,
  getFileIcon,
  getInitials,
  generateColor,
  truncate,
} from "./utils";

describe("formatFileSize", () => {
  it("test_formatFileSize_zeroBytes_returnsPlainZeroB", () => {
    expect(formatFileSize(0)).toBe("0 B");
  });

  it("test_formatFileSize_oneByteBelowAKilobyte_staysInBytes", () => {
    expect(formatFileSize(1023)).toBe("1023 B");
  });

  it("test_formatFileSize_exactlyOneKilobyte_switchesToKB", () => {
    expect(formatFileSize(1024)).toBe("1 KB");
  });

  it("test_formatFileSize_oneByteAboveAKilobyte_roundsToOneDecimal", () => {
    expect(formatFileSize(1025)).toBe("1 KB");
  });

  it("test_formatFileSize_theHundredMegabyteUploadCap_readsAs100MB", () => {
    // file-service caps uploads at 104857600 bytes; the number a user sees next
    // to the limit has to be the same number the limit is expressed in.
    expect(formatFileSize(104857600)).toBe("100 MB");
  });

  it("test_formatFileSize_oneByteOverTheUploadCap_stillReadsAs100MB", () => {
    // The rejected file and the cap render identically, so "100 MB" in an error
    // message cannot be read as "my file was under the limit".
    expect(formatFileSize(104857601)).toBe("100 MB");
  });

  it("test_formatFileSize_terabyteScale_usesTheLargestKnownUnit", () => {
    expect(formatFileSize(1024 ** 4)).toBe("1 TB");
  });

  it("test_formatFileSize_beyondTerabytes_producesUndefinedUnit", () => {
    // sizes[] stops at TB, so anything petabyte-scale indexes past the end of the
    // array. Pinned rather than fixed: the fix is production code.
    expect(formatFileSize(1024 ** 5)).toBe("1 undefined");
  });

  it("test_formatFileSize_negativeSize_producesNaN", () => {
    // Math.log of a negative number is NaN. No caller should pass one, but the
    // output is a user-visible string, so the failure mode is worth pinning.
    expect(formatFileSize(-1)).toContain("NaN");
  });
});

describe("formatRelativeTime", () => {
  const now = new Date("2026-03-15T12:00:00.000Z");

  afterEach(() => {
    vi.useRealTimers();
  });

  function at(iso: string): string {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    return formatRelativeTime(iso);
  }

  it("test_formatRelativeTime_fiftyNineSecondsAgo_saysJustNow", () => {
    expect(at("2026-03-15T11:59:01.000Z")).toBe("just now");
  });

  it("test_formatRelativeTime_exactlySixtySecondsAgo_switchesToMinutes", () => {
    expect(at("2026-03-15T11:59:00.000Z")).toBe("1m ago");
  });

  it("test_formatRelativeTime_fiftyNineMinutesAgo_staysInMinutes", () => {
    expect(at("2026-03-15T11:01:00.000Z")).toBe("59m ago");
  });

  it("test_formatRelativeTime_exactlySixtyMinutesAgo_switchesToHours", () => {
    expect(at("2026-03-15T11:00:00.000Z")).toBe("1h ago");
  });

  it("test_formatRelativeTime_twentyThreeHoursAgo_staysInHours", () => {
    expect(at("2026-03-14T13:00:00.000Z")).toBe("23h ago");
  });

  it("test_formatRelativeTime_exactlyTwentyFourHoursAgo_switchesToDays", () => {
    expect(at("2026-03-14T12:00:00.000Z")).toBe("1d ago");
  });

  it("test_formatRelativeTime_sixDaysAgo_staysInDays", () => {
    expect(at("2026-03-09T12:00:00.000Z")).toBe("6d ago");
  });

  it("test_formatRelativeTime_exactlySevenDaysAgo_fallsBackToAnAbsoluteDate", () => {
    expect(at("2026-03-08T12:00:00.000Z")).not.toMatch(/ago$/);
  });

  it("test_formatRelativeTime_aTimestampInTheFuture_saysJustNow", () => {
    // Clock skew between a client and the services routinely produces created_at
    // values a few seconds ahead of the browser. diffSeconds goes negative, which
    // is still < 60, so the user sees "just now" rather than "-1m ago".
    expect(at("2026-03-15T12:00:30.000Z")).toBe("just now");
  });

  it("test_formatRelativeTime_farFutureTimestamp_saysJustNow", () => {
    // Every branch tests an upper bound only, so a timestamp a year out also
    // lands in the "just now" arm rather than an absolute date.
    expect(at("2027-03-15T12:00:00.000Z")).toBe("just now");
  });

  it("test_formatRelativeTime_daylightSavingTransition_measuresElapsedNotCalendarTime", () => {
    // US DST began 2026-03-08. An instant 23 real hours before noon on the 15th is
    // still "23h ago" regardless of the runner's local zone, because the maths runs
    // on epoch milliseconds.
    expect(at("2026-03-14T13:00:00.000Z")).toBe("23h ago");
  });

  it("test_formatRelativeTime_unparseableDate_rendersInvalidDateRatherThanThrowing", () => {
    // Every comparison against NaN is false, so control falls through to
    // toLocaleDateString and the user sees the literal string "Invalid Date"
    // in a timestamp column instead of the component crashing.
    expect(() => at("not-a-date")).not.toThrow();
    expect(at("not-a-date")).toBe("Invalid Date");
  });

  it("test_formatRelativeTime_emptyString_rendersInvalidDateRatherThanThrowing", () => {
    expect(at("")).toBe("Invalid Date");
  });
});

describe("truncate", () => {
  it("test_truncate_stringOneShorterThanTheLimit_isUnchanged", () => {
    expect(truncate("abcdefghi", 10)).toBe("abcdefghi");
  });

  it("test_truncate_stringExactlyAtTheLimit_isUnchanged", () => {
    expect(truncate("abcdefghij", 10)).toBe("abcdefghij");
  });

  it("test_truncate_stringOneOverTheLimit_isCutToTheLimit", () => {
    const result = truncate("abcdefghijk", 10);
    expect(result).toBe("abcdefg...");
    expect(result.length).toBe(10);
  });

  it("test_truncate_emptyString_isUnchanged", () => {
    expect(truncate("", 10)).toBe("");
  });

  it("test_truncate_limitSmallerThanTheEllipsis_returnsAStringLongerThanTheLimit", () => {
    // maxLength - 3 goes negative, so slice() counts back from the end instead of
    // forward from the start and the result is longer than the limit that was
    // asked for. Pinned rather than fixed: the fix is production code.
    const result = truncate("abcdefgh", 2);
    expect(result).toBe("abcdefg...");
    expect(result.length).toBeGreaterThan(2);
  });

  it("test_truncate_multibyteCharacters_cutsOnUTF16UnitsNotCodepoints", () => {
    // "🦦" is a surrogate pair, so a 5-unit cut can split it and leave a lone
    // surrogate. Documented because file names in this app are user-supplied.
    const result = truncate("🦦🦦🦦🦦", 5);
    expect(result.length).toBe(5);
    expect([...result].length).toBeLessThan(5);
  });
});

describe("getInitials", () => {
  it("test_getInitials_firstAndLastName_returnsTwoUppercaseLetters", () => {
    expect(getInitials("ada lovelace")).toBe("AL");
  });

  it("test_getInitials_singleName_returnsOneLetter", () => {
    expect(getInitials("Ada")).toBe("A");
  });

  it("test_getInitials_threeOrMoreNames_isCappedAtTwoLetters", () => {
    expect(getInitials("Ada Byron King Lovelace")).toBe("AB");
  });

  it("test_getInitials_nonLatinName_usesTheFirstCharacterOfEachWord", () => {
    expect(getInitials("Ольга Петрова")).toBe("ОП");
  });

  it("test_getInitials_emptyString_returnsEmptyString", () => {
    expect(getInitials("")).toBe("");
  });

  it("test_getInitials_doubleSpacedName_ignoresTheEmptySegment", () => {
    // split(" ") yields an empty segment whose [0] is undefined; join() renders
    // undefined as "", so the extra space is harmless.
    expect(getInitials("Ada  Lovelace")).toBe("AL");
  });

  it("test_getInitials_trailingSpace_returnsOnlyTheFirstInitial", () => {
    // A user mid-typing a display name produces a trailing space, and the empty
    // trailing segment contributes nothing, so the avatar shows one letter.
    expect(getInitials("Ada ")).toBe("A");
  });

  it("test_getInitials_whitespaceOnlyName_returnsEmptyString", () => {
    expect(getInitials("   ")).toBe("");
  });
});

describe("getFileIcon", () => {
  it("test_getFileIcon_imageMimeType_returnsImage", () => {
    expect(getFileIcon("image/png")).toBe("image");
  });

  it("test_getFileIcon_pdf_returnsFileText", () => {
    expect(getFileIcon("application/pdf")).toBe("file-text");
  });

  it("test_getFileIcon_officeSpreadsheet_returnsTable", () => {
    expect(getFileIcon("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
      .toBe("table");
  });

  it("test_getFileIcon_unknownMimeType_fallsBackToGenericFile", () => {
    expect(getFileIcon("application/x-otter")).toBe("file");
  });

  it("test_getFileIcon_emptyMimeType_fallsBackToGenericFile", () => {
    expect(getFileIcon("")).toBe("file");
  });

  it("test_getFileIcon_mimeTypeContainingDocumentAnywhere_matchesFileText", () => {
    // The checks are substring matches, not prefix matches, so a type merely
    // containing "document" is classified as a text document.
    expect(getFileIcon("application/vnd.oasis.opendocument.graphics")).toBe("file-text");
  });
});

describe("generateColor", () => {
  it("test_generateColor_sameSeed_isStableAcrossCalls", () => {
    expect(generateColor("ada@otterworks.test")).toBe(generateColor("ada@otterworks.test"));
  });

  it("test_generateColor_anySeed_returnsAColourFromThePalette", () => {
    const palette = new Set([
      "#ef4444", "#f97316", "#eab308", "#22c55e",
      "#06b6d4", "#3b82f6", "#8b5cf6", "#ec4899",
    ]);
    for (const seed of ["", "a", "🦦", "Ольга", "a".repeat(1000)]) {
      expect(palette.has(generateColor(seed))).toBe(true);
    }
  });

  it("test_generateColor_emptySeed_doesNotThrow", () => {
    expect(() => generateColor("")).not.toThrow();
  });
});

describe("cn", () => {
  it("test_cn_falsyValues_areDropped", () => {
    expect(cn("a", false && "b", null, undefined, "c")).toBe("a c");
  });

  it("test_cn_noArguments_returnsEmptyString", () => {
    expect(cn()).toBe("");
  });
});
