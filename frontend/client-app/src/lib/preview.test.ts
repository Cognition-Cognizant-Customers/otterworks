// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { capRows, getPreviewKind, MAX_TABLE_ROWS, parseCsv, sanitizeDocHtml } from "./preview";

// OTD-12 — traces to AC-02..AC-10 (preview-kind dispatch), AC-05 (CSV parsing),
// AC-12/AC-18 (row capping), AC-07 (docx HTML sanitisation).

describe("getPreviewKind (AC-02..AC-10)", () => {
  it("maps images", () => {
    expect(getPreviewKind("image/png")).toBe("image");
    expect(getPreviewKind("image/jpeg")).toBe("image");
  });

  it("maps video and audio", () => {
    expect(getPreviewKind("video/mp4")).toBe("video");
    expect(getPreviewKind("audio/mpeg")).toBe("audio");
  });

  it("maps PDF", () => {
    expect(getPreviewKind("application/pdf")).toBe("pdf");
  });

  it("maps CSV to table, ahead of generic text", () => {
    expect(getPreviewKind("text/csv")).toBe("csv");
  });

  it("maps xlsx to spreadsheet", () => {
    expect(
      getPreviewKind("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    ).toBe("spreadsheet");
  });

  it("maps docx", () => {
    expect(
      getPreviewKind("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    ).toBe("docx");
  });

  it("maps text and code types", () => {
    expect(getPreviewKind("text/markdown")).toBe("text");
    expect(getPreviewKind("text/plain")).toBe("text");
    expect(getPreviewKind("application/json")).toBe("text");
    expect(getPreviewKind("application/x-yaml")).toBe("text");
  });

  it("falls back for pptx, archives and unknown types (AC-10)", () => {
    expect(
      getPreviewKind("application/vnd.openxmlformats-officedocument.presentationml.presentation")
    ).toBe("fallback");
    expect(getPreviewKind("application/zip")).toBe("fallback");
    expect(getPreviewKind("application/octet-stream")).toBe("fallback");
    expect(getPreviewKind("")).toBe("fallback");
  });
});

describe("parseCsv (AC-05)", () => {
  it("parses simple rows", () => {
    expect(parseCsv("a,b,c\n1,2,3")).toEqual([
      ["a", "b", "c"],
      ["1", "2", "3"],
    ]);
  });

  it("handles quoted fields with commas and escaped quotes", () => {
    expect(parseCsv('name,note\n"Doe, J","said ""hi"""')).toEqual([
      ["name", "note"],
      ["Doe, J", 'said "hi"'],
    ]);
  });

  it("handles CRLF line endings and quoted newlines", () => {
    expect(parseCsv('a,b\r\n"line1\nline2",2\r\n')).toEqual([
      ["a", "b"],
      ["line1\nline2", "2"],
    ]);
  });

  it("ignores a trailing newline and handles empty input", () => {
    expect(parseCsv("a,b\n1,2\n")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
    expect(parseCsv("")).toEqual([]);
  });
});

describe("capRows (AC-12, AC-18)", () => {
  it("returns rows untouched under the cap", () => {
    const rows = [["a"], ["b"]];
    expect(capRows(rows)).toEqual({ rows, truncated: false });
  });

  it("caps rows above the limit and flags truncation", () => {
    const rows = Array.from({ length: MAX_TABLE_ROWS + 10 }, (_, i) => [String(i)]);
    const capped = capRows(rows);
    expect(capped.rows).toHaveLength(MAX_TABLE_ROWS);
    expect(capped.truncated).toBe(true);
  });
});

describe("sanitizeDocHtml (AC-07)", () => {
  it("keeps benign formatting markup", () => {
    const html = "<h1>Title</h1><p>Body <strong>bold</strong></p>";
    expect(sanitizeDocHtml(html)).toContain("<h1>Title</h1>");
    expect(sanitizeDocHtml(html)).toContain("<strong>bold</strong>");
  });

  it("strips script/style/iframe tags and event handlers", () => {
    const html =
      '<p onclick="x()">hi</p><script>alert(1)</script><iframe src="x"></iframe><style>p{}</style>';
    const out = sanitizeDocHtml(html);
    expect(out).not.toContain("<script");
    expect(out).not.toContain("<iframe");
    expect(out).not.toContain("<style");
    expect(out).not.toContain("onclick");
    expect(out).toContain("hi");
  });

  it("neutralises javascript: URLs", () => {
    const out = sanitizeDocHtml('<a href="javascript:alert(1)">x</a>');
    expect(out).not.toContain("javascript:");
  });
});
