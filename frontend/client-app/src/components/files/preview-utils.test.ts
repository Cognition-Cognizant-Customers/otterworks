import { describe, it, expect } from "vitest";
import {
  getPreviewKind,
  SPREADSHEET_ROW_CAP,
  capSpreadsheetRows,
} from "./preview-utils";

// BDD-02..BDD-10 (AC-02..AC-10): renderer selection per mime type
describe("getPreviewKind", () => {
  it("selects image for image/* (AC-02/BDD-02)", () => {
    expect(getPreviewKind("image/png")).toBe("image");
    expect(getPreviewKind("image/jpeg")).toBe("image");
  });

  it("selects video for video/* (AC-08/BDD-08)", () => {
    expect(getPreviewKind("video/mp4")).toBe("video");
  });

  it("selects audio for audio/* (AC-09/BDD-09)", () => {
    expect(getPreviewKind("audio/mpeg")).toBe("audio");
    expect(getPreviewKind("audio/wav")).toBe("audio");
  });

  it("selects pdf for application/pdf (AC-03/BDD-03)", () => {
    expect(getPreviewKind("application/pdf")).toBe("pdf");
  });

  it("selects spreadsheet for xlsx and csv (AC-05/AC-06, BDD-05/BDD-06)", () => {
    expect(
      getPreviewKind(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      )
    ).toBe("spreadsheet");
    expect(getPreviewKind("application/vnd.ms-excel")).toBe("spreadsheet");
    expect(getPreviewKind("text/csv")).toBe("spreadsheet");
  });

  it("selects docx for Word documents (AC-07/BDD-07)", () => {
    expect(
      getPreviewKind(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      )
    ).toBe("docx");
  });

  it("selects text for text-like mime types (AC-04/BDD-04)", () => {
    expect(getPreviewKind("text/plain")).toBe("text");
    expect(getPreviewKind("text/markdown")).toBe("text");
    expect(getPreviewKind("application/json")).toBe("text");
    expect(getPreviewKind("application/x-yaml")).toBe("text");
  });

  it("falls back for pptx and unknown types (AC-10/BDD-10)", () => {
    expect(
      getPreviewKind(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
      )
    ).toBe("none");
    expect(getPreviewKind("application/octet-stream")).toBe("none");
    expect(getPreviewKind("application/zip")).toBe("none");
  });
});

// BDD-13 (AC-13) / BDD-18 (AC-18): spreadsheet row cap
describe("capSpreadsheetRows", () => {
  const row = (i: number) => [`cell-${i}`];

  it("returns all rows when under the cap", () => {
    const rows = Array.from({ length: 10 }, (_, i) => row(i));
    const { rows: capped, truncated } = capSpreadsheetRows(rows);
    expect(capped).toHaveLength(10);
    expect(truncated).toBe(false);
  });

  it("caps rows and flags truncation when over the cap (AC-13/BDD-13)", () => {
    const rows = Array.from({ length: SPREADSHEET_ROW_CAP + 50 }, (_, i) =>
      row(i)
    );
    const { rows: capped, truncated } = capSpreadsheetRows(rows);
    expect(capped).toHaveLength(SPREADSHEET_ROW_CAP);
    expect(truncated).toBe(true);
  });
});
