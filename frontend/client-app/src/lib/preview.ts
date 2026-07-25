import DOMPurify from "dompurify";

export type PreviewKind =
  | "image"
  | "video"
  | "audio"
  | "pdf"
  | "csv"
  | "spreadsheet"
  | "docx"
  | "text"
  | "fallback";

const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

const TEXT_MIMES = new Set([
  "application/json",
  "application/xml",
  "application/javascript",
  "application/typescript",
  "application/x-yaml",
  "application/x-sh",
]);

export function getPreviewKind(mimeType: string): PreviewKind {
  if (!mimeType) return "fallback";
  if (mimeType.startsWith("image/")) return "image";
  if (mimeType.startsWith("video/")) return "video";
  if (mimeType.startsWith("audio/")) return "audio";
  if (mimeType === "application/pdf") return "pdf";
  if (mimeType === "text/csv") return "csv";
  if (mimeType === XLSX_MIME) return "spreadsheet";
  if (mimeType === DOCX_MIME) return "docx";
  if (mimeType.startsWith("text/") || TEXT_MIMES.has(mimeType)) return "text";
  return "fallback";
}

/** RFC-4180-style CSV parser: quoted fields, escaped quotes, embedded newlines, CRLF. */
export function parseCsv(text: string): string[][] {
  if (!text) return [];
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      field = "";
      rows.push(row);
      row = [];
    } else {
      field += ch;
    }
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

export const MAX_TABLE_ROWS = 500;

export function capRows<T>(
  rows: T[],
  max: number = MAX_TABLE_ROWS
): { rows: T[]; truncated: boolean } {
  if (rows.length <= max) return { rows, truncated: false };
  return { rows: rows.slice(0, max), truncated: true };
}

/**
 * Sanitises converted document HTML (mammoth output) before it is injected
 * into the DOM: strips scripts, inline event handlers, javascript: URLs and
 * other active content via DOMPurify.
 */
export function sanitizeDocHtml(html: string): string {
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["style", "form"],
  });
}
