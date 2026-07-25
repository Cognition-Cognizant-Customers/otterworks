import { describe, it, expect } from "vitest";
import { getPreviewKind, getFileExtension, parseDelimited } from "./file-preview";

describe("getPreviewKind", () => {
  it("classifies images by MIME and by extension", () => {
    expect(getPreviewKind("image/png", "logo.png")).toBe("image");
    expect(getPreviewKind("application/octet-stream", "photo.jpg")).toBe("image");
    expect(getPreviewKind("image/svg+xml", "icon.svg")).toBe("image");
  });

  it("classifies video and audio", () => {
    expect(getPreviewKind("video/mp4", "clip.mp4")).toBe("video");
    expect(getPreviewKind("application/octet-stream", "clip.mov")).toBe("video");
    expect(getPreviewKind("audio/mpeg", "song.mp3")).toBe("audio");
    expect(getPreviewKind("", "voice.wav")).toBe("audio");
  });

  it("classifies PDFs", () => {
    expect(getPreviewKind("application/pdf", "doc.pdf")).toBe("pdf");
    expect(getPreviewKind("application/octet-stream", "report.pdf")).toBe("pdf");
  });

  it("classifies CSV/TSV as csv for table rendering", () => {
    expect(getPreviewKind("text/csv", "data.csv")).toBe("csv");
    expect(getPreviewKind("application/octet-stream", "data.tsv")).toBe("csv");
  });

  it("classifies text and code files", () => {
    expect(getPreviewKind("text/plain", "notes.txt")).toBe("text");
    expect(getPreviewKind("application/json", "package.json")).toBe("text");
    expect(getPreviewKind("application/octet-stream", "main.ts")).toBe("text");
    expect(getPreviewKind("", "server.py")).toBe("text");
  });

  it("classifies office documents", () => {
    expect(getPreviewKind("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "a.docx")).toBe("office");
    expect(getPreviewKind("application/octet-stream", "sheet.xlsx")).toBe("office");
    expect(getPreviewKind("application/octet-stream", "deck.pptx")).toBe("office");
  });

  it("falls back to unknown for unsupported types", () => {
    expect(getPreviewKind("application/zip", "archive.zip")).toBe("unknown");
    expect(getPreviewKind("application/octet-stream", "binary.bin")).toBe("unknown");
    expect(getPreviewKind("", "")).toBe("unknown");
  });
});

describe("getFileExtension", () => {
  it("extracts a lowercase extension", () => {
    expect(getFileExtension("Report.PDF")).toBe("pdf");
    expect(getFileExtension("a.b.c.tar.gz")).toBe("gz");
  });

  it("returns empty for dotfiles and extensionless names", () => {
    expect(getFileExtension(".gitignore")).toBe("");
    expect(getFileExtension("README")).toBe("");
    expect(getFileExtension("trailing.")).toBe("");
  });
});

describe("parseDelimited", () => {
  it("parses simple CSV rows", () => {
    expect(parseDelimited("a,b,c\n1,2,3")).toEqual([
      ["a", "b", "c"],
      ["1", "2", "3"],
    ]);
  });

  it("honors quoted fields containing commas and quotes", () => {
    expect(parseDelimited('name,note\n"Doe, Jane","said ""hi"""')).toEqual([
      ["name", "note"],
      ["Doe, Jane", 'said "hi"'],
    ]);
  });

  it("supports tab-delimited input", () => {
    expect(parseDelimited("a\tb\n1\t2", "\t")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });
});
