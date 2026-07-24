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

export const MAX_OFFICE_PREVIEW_SIZE = 20 * 1024 * 1024; // 20 MB

export class PreviewTooLargeError extends Error {}

export async function fetchOfficeBuffer(url: string): Promise<ArrayBuffer> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const contentLength = Number(res.headers.get("content-length"));
  if (contentLength > MAX_OFFICE_PREVIEW_SIZE) {
    res.body?.cancel();
    throw new PreviewTooLargeError();
  }
  if (!res.body) return res.arrayBuffer();

  // Enforce the cap while streaming, since Content-Length may be absent.
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    received += value.length;
    if (received > MAX_OFFICE_PREVIEW_SIZE) {
      await reader.cancel();
      throw new PreviewTooLargeError();
    }
    chunks.push(value);
  }
  const buffer = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    buffer.set(chunk, offset);
    offset += chunk.length;
  }
  return buffer.buffer;
}

export function capSpreadsheetRows<T>(rows: T[]): {
  rows: T[];
  truncated: boolean;
} {
  if (rows.length <= SPREADSHEET_ROW_CAP) return { rows, truncated: false };
  return { rows: rows.slice(0, SPREADSHEET_ROW_CAP), truncated: true };
}
