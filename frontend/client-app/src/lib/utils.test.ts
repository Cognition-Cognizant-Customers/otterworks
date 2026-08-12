import { describe, expect, it } from "vitest";
import {
  formatFileSize,
  formatRelativeTime,
  generateColor,
  getFileIcon,
  getInitials,
  truncate,
} from "./utils";

describe("utility formatting", () => {
  it.each([
    [0, "0 B"],
    [1024, "1 KB"],
    [1_572_864, "1.5 MB"],
    [1024 ** 3, "1 GB"],
  ])("formats %s bytes as %s", (bytes, expected) => {
    expect(formatFileSize(bytes)).toBe(expected);
  });

  it("classifies common file types and falls back to a generic icon", () => {
    expect(getFileIcon("image/png")).toBe("image");
    expect(getFileIcon("application/pdf")).toBe("file-text");
    expect(getFileIcon("application/zip")).toBe("archive");
    expect(getFileIcon("application/octet-stream")).toBe("file");
  });

  it("builds bounded initials and truncates only overlong values", () => {
    expect(getInitials("Ada Lovelace Example")).toBe("AL");
    expect(truncate("short", 5)).toBe("short");
    expect(truncate("OtterWorks", 7)).toBe("Otte...");
  });

  it("assigns deterministic colors", () => {
    expect(generateColor("user-42")).toBe(generateColor("user-42"));
    expect(generateColor("user-42")).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("formats recent dates into the appropriate relative buckets", () => {
    const now = Date.now();
    expect(formatRelativeTime(new Date(now - 30_000).toISOString())).toBe("just now");
    expect(formatRelativeTime(new Date(now - 5 * 60_000).toISOString())).toBe("5m ago");
    expect(formatRelativeTime(new Date(now - 2 * 60 * 60_000).toISOString())).toBe("2h ago");
    expect(formatRelativeTime(new Date(now - 3 * 24 * 60 * 60_000).toISOString())).toBe("3d ago");
  });
});
