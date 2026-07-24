export type PreviewKind =
  | "image"
  | "video"
  | "audio"
  | "pdf"
  | "spreadsheet"
  | "docx"
  | "text"
  | "none";

const SPREADSHEET_MIME_TYPES = new Set([
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "text/csv",
]);

const DOCX_MIME_TYPES = new Set([
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);

const TEXT_MIME_TYPES = new Set([
  "application/json",
  "application/xml",
  "application/javascript",
  "application/typescript",
  "application/x-yaml",
  "application/x-sh",
]);

/** Pick the preview renderer for a file's mime type. */
export function getPreviewKind(mimeType: string): PreviewKind {
  if (mimeType.startsWith("image/")) return "image";
  if (mimeType.startsWith("video/")) return "video";
  if (mimeType.startsWith("audio/")) return "audio";
  if (mimeType === "application/pdf") return "pdf";
  if (SPREADSHEET_MIME_TYPES.has(mimeType)) return "spreadsheet";
  if (DOCX_MIME_TYPES.has(mimeType)) return "docx";
  if (mimeType.startsWith("text/") || TEXT_MIME_TYPES.has(mimeType))
    return "text";
  return "none";
}

/** Maximum spreadsheet rows rendered before truncation. */
export const SPREADSHEET_ROW_CAP = 500;

export function capSpreadsheetRows<T>(rows: T[]): {
  rows: T[];
  truncated: boolean;
} {
  if (rows.length <= SPREADSHEET_ROW_CAP) return { rows, truncated: false };
  return { rows: rows.slice(0, SPREADSHEET_ROW_CAP), truncated: true };
}
