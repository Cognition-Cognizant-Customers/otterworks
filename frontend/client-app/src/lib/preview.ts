// Shared helpers for deciding how a file should be previewed. Kept UI-free so
// it can be unit-tested and reused by the Files-list modal and the detail page.

export type PreviewKind =
  | "image"
  | "video"
  | "audio"
  | "pdf"
  | "text"
  | "spreadsheet"
  | "unsupported";

const TEXT_MIME_TYPES = new Set([
  "application/json",
  "application/xml",
  "application/javascript",
  "application/typescript",
  "application/x-yaml",
  "application/x-sh",
]);

const TEXT_EXTENSIONS = new Set([
  "txt",
  "csv",
  "md",
  "markdown",
  "json",
  "xml",
  "yaml",
  "yml",
  "js",
  "ts",
  "jsx",
  "tsx",
  "sh",
  "log",
]);

const SPREADSHEET_MIME_TYPES = new Set([
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
]);

function extensionOf(fileName?: string): string {
  if (!fileName) return "";
  const parts = fileName.split(".");
  return parts.length > 1 ? parts[parts.length - 1].toLowerCase() : "";
}

/** Determine which preview renderer to use for a file, from MIME type (+ name). */
export function getPreviewKind(mimeType: string, fileName?: string): PreviewKind {
  const m = (mimeType || "").toLowerCase();
  const ext = extensionOf(fileName);

  if (m.startsWith("image/")) return "image";
  if (m.startsWith("video/")) return "video";
  if (m.startsWith("audio/")) return "audio";
  if (m === "application/pdf") return "pdf";

  if (SPREADSHEET_MIME_TYPES.has(m) || ext === "xlsx" || ext === "xls") {
    return "spreadsheet";
  }

  if (m.startsWith("text/") || TEXT_MIME_TYPES.has(m) || TEXT_EXTENSIONS.has(ext)) {
    return "text";
  }

  return "unsupported";
}

/** Whether a file type has a dedicated inline renderer (vs. generic fallback). */
export function isPreviewable(mimeType: string, fileName?: string): boolean {
  return getPreviewKind(mimeType, fileName) !== "unsupported";
}
