// Shared, framework-agnostic logic for classifying files into preview kinds.
// Kept out of the React component so it can be unit-tested in isolation.

export type PreviewKind =
  | "image"
  | "video"
  | "audio"
  | "pdf"
  | "text"
  | "csv"
  | "office"
  | "unknown";

// MIME types that are plain-text under the hood even though they aren't `text/*`.
const TEXT_MIME_TYPES = new Set([
  "application/json",
  "application/xml",
  "application/javascript",
  "application/typescript",
  "application/x-yaml",
  "application/yaml",
  "application/x-sh",
  "application/x-httpd-php",
  "application/sql",
]);

// Extensions that should render as source/text even when the server reports a
// generic MIME type (common with seeded data and `application/octet-stream`).
const TEXT_EXTENSIONS = new Set([
  "txt", "md", "markdown", "log", "json", "xml", "yaml", "yml", "toml", "ini",
  "cfg", "conf", "env", "js", "jsx", "ts", "tsx", "mjs", "cjs", "css", "scss",
  "less", "html", "htm", "svg", "py", "rb", "go", "rs", "java", "kt", "kts",
  "c", "h", "cpp", "cc", "hpp", "cs", "php", "swift", "scala", "sh", "bash",
  "zsh", "sql", "graphql", "gql", "dockerfile", "makefile", "gradle", "properties",
]);

const OFFICE_EXTENSIONS = new Set([
  "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf",
]);

const OFFICE_MIME_HINTS = [
  "word",
  "excel",
  "spreadsheet",
  "presentation",
  "powerpoint",
  "officedocument",
  "msword",
  "opendocument",
];

export function getFileExtension(fileName: string): string {
  const clean = fileName.split(/[?#]/)[0];
  const dot = clean.lastIndexOf(".");
  if (dot <= 0 || dot === clean.length - 1) return "";
  return clean.slice(dot + 1).toLowerCase();
}

/**
 * Classify a file into a preview kind using both its MIME type and file name.
 * MIME type wins when it is specific; otherwise we fall back to the extension
 * so previews still work for files served as `application/octet-stream`.
 */
export function getPreviewKind(mimeType: string, fileName = ""): PreviewKind {
  const mime = (mimeType || "").toLowerCase();
  const ext = getFileExtension(fileName);

  if (mime.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif"].includes(ext)) {
    return "image";
  }
  if (mime.startsWith("video/") || ["mp4", "webm", "ogv", "mov", "m4v"].includes(ext)) {
    return "video";
  }
  if (mime.startsWith("audio/") || ["mp3", "wav", "ogg", "m4a", "flac", "aac"].includes(ext)) {
    return "audio";
  }
  if (mime === "application/pdf" || ext === "pdf") {
    return "pdf";
  }
  if (mime === "text/csv" || mime === "application/csv" || ext === "csv" || ext === "tsv") {
    return "csv";
  }
  if (OFFICE_EXTENSIONS.has(ext) || OFFICE_MIME_HINTS.some((hint) => mime.includes(hint))) {
    return "office";
  }
  if (mime.startsWith("text/") || TEXT_MIME_TYPES.has(mime) || TEXT_EXTENSIONS.has(ext)) {
    return "text";
  }
  return "unknown";
}

/** Parse delimited text (CSV/TSV) into rows, honoring quoted fields. */
export function parseDelimited(input: string, delimiter = ","): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;

  for (let i = 0; i < input.length; i++) {
    const char = input[i];
    if (inQuotes) {
      if (char === '"') {
        if (input[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += char;
      }
      continue;
    }
    if (char === '"') {
      inQuotes = true;
    } else if (char === delimiter) {
      row.push(field);
      field = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && input[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += char;
    }
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.length > 1 || (r.length === 1 && r[0].trim() !== ""));
}
