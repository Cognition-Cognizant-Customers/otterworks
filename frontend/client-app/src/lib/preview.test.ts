import { describe, it, expect } from "vitest";
import { getPreviewKind, isPreviewable } from "./preview";

describe("getPreviewKind", () => {
  it("detects images, video and audio by mime prefix", () => {
    expect(getPreviewKind("image/png")).toBe("image");
    expect(getPreviewKind("image/jpeg")).toBe("image");
    expect(getPreviewKind("video/mp4")).toBe("video");
    expect(getPreviewKind("audio/mpeg")).toBe("audio");
  });

  it("detects PDF", () => {
    expect(getPreviewKind("application/pdf")).toBe("pdf");
  });

  it("detects spreadsheets by mime and by extension", () => {
    expect(
      getPreviewKind(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      )
    ).toBe("spreadsheet");
    expect(getPreviewKind("application/vnd.ms-excel")).toBe("spreadsheet");
    // octet-stream but .xlsx name still routes to spreadsheet
    expect(getPreviewKind("application/octet-stream", "report.xlsx")).toBe(
      "spreadsheet"
    );
  });

  it("detects text/code/csv/json/markdown", () => {
    expect(getPreviewKind("text/plain")).toBe("text");
    expect(getPreviewKind("text/csv")).toBe("text");
    expect(getPreviewKind("text/markdown")).toBe("text");
    expect(getPreviewKind("application/json")).toBe("text");
    expect(getPreviewKind("application/octet-stream", "notes.md")).toBe("text");
  });

  it("falls back to unsupported for office docs and unknown types", () => {
    expect(
      getPreviewKind(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      )
    ).toBe("unsupported");
    expect(
      getPreviewKind(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
      )
    ).toBe("unsupported");
    expect(getPreviewKind("application/octet-stream")).toBe("unsupported");
  });

  it("isPreviewable mirrors getPreviewKind", () => {
    expect(isPreviewable("image/png")).toBe(true);
    expect(isPreviewable("application/octet-stream")).toBe(false);
  });
});
