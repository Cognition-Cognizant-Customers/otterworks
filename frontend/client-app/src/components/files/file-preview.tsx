import { useState, useEffect, useRef, useCallback } from "react";
import {
  File,
  AlertCircle,
  Download,
  X,
  Loader2,
  Music,
} from "lucide-react";
import type * as XLSXType from "xlsx";
import { filesApi } from "@/lib/api";
import { getPreviewKind } from "@/lib/preview";
import { formatFileSize } from "@/lib/utils";

const MAX_PREVIEW_SIZE = 500_000; // 500 KB — truncate beyond this
const MAX_SPREADSHEET_BYTES = 15_000_000; // 15 MB — skip client-side parse beyond this
// Image/PDF/video/audio load the whole file into the browser as a blob, so cap
// the size we buffer and offer a download fallback beyond it. Kept in sync with
// the file-service MAX_PREVIEW_BYTES (which rejects larger full requests with 413).
const MAX_MEDIA_BYTES = 25 * 1024 * 1024; // 25 MB
const MAX_SHEET_ROWS = 1000;
const MAX_SHEET_COLS = 50;

/** Minimal file shape the preview components need. */
export interface PreviewFile {
  id: string;
  name: string;
  mimeType: string;
  size: number;
}

// Loads file bytes from the same-origin content endpoint as an object URL and
// revokes it on cleanup. Used for image/pdf/video/audio inline rendering.
function useContentBlobUrl(fileId: string, enabled = true) {
  const [url, setUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let created: string | null = null;
    const controller = new AbortController();
    setStatus("loading");
    setUrl(null);

    filesApi
      .getContentBlobUrl(fileId, { signal: controller.signal })
      .then((u) => {
        if (cancelled) {
          URL.revokeObjectURL(u);
          return;
        }
        created = u;
        setUrl(u);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (!cancelled && (err as Error)?.name !== "AbortError") setStatus("error");
      });

    return () => {
      cancelled = true;
      controller.abort();
      if (created) URL.revokeObjectURL(created);
    };
  }, [fileId, enabled]);

  return { url, status };
}

// Shown when a file exceeds the inline-preview size cap; offers a download.
function PreviewTooLarge({
  file,
  onDownload,
}: {
  file: PreviewFile;
  onDownload: () => void;
}) {
  return (
    <div className="text-center py-10">
      <AlertCircle size={48} className="text-gray-300 mx-auto mb-3" />
      <p className="text-sm text-gray-600 font-medium">{file.name}</p>
      <p className="text-xs text-gray-400 mt-1">{formatFileSize(file.size)}</p>
      <p className="text-sm text-gray-500 mt-3">
        This file is too large to preview here. Download it to view the full contents.
      </p>
      <button
        onClick={onDownload}
        className="mt-4 inline-flex items-center gap-2 px-4 py-2 text-sm text-white bg-otter-600 rounded-lg hover:bg-otter-700 transition"
      >
        <Download size={16} />
        Download
      </button>
    </div>
  );
}

function PreviewSpinner({ label = "Loading preview…" }: { label?: string }) {
  return (
    <div className="w-full text-center py-8">
      <div className="w-6 h-6 border-2 border-otter-600 border-t-transparent rounded-full animate-spin mx-auto" />
      <p className="text-xs text-gray-400 mt-2">{label}</p>
    </div>
  );
}

function PreviewError({ message }: { message: string }) {
  return (
    <div className="text-center py-8">
      <AlertCircle size={48} className="text-gray-300 mx-auto mb-3" />
      <p className="text-sm text-gray-500">{message}</p>
    </div>
  );
}

interface TextFilePreviewProps {
  presignedUrl?: string;
  fileName: string;
}

export function TextFilePreview({ presignedUrl, fileName }: TextFilePreviewProps) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [truncated, setTruncated] = useState(false);
  // Falls back to iframe when fetch() is blocked (e.g. CORS on cross-origin S3 URLs)
  const [useIframeFallback, setUseIframeFallback] = useState(false);

  useEffect(() => {
    if (!presignedUrl) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setContent(null);
    setUseIframeFallback(false);

    fetch(presignedUrl, {
      headers: { Range: `bytes=0-${MAX_PREVIEW_SIZE - 1}` },
    })
      .then(async (res) => {
        // 206 = partial content (Range honored), 200 = full file (Range ignored)
        if (!res.ok && res.status !== 206) throw new Error(`HTTP ${res.status}`);
        const text = await res.text();
        if (cancelled) return;
        setContent(text);
        setTruncated(res.status === 206);
      })
      .catch(() => {
        if (!cancelled) setUseIframeFallback(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [presignedUrl]);

  if (loading) {
    return (
      <div className="w-full text-center py-8">
        <div className="w-6 h-6 border-2 border-otter-600 border-t-transparent rounded-full animate-spin mx-auto" />
        <p className="text-xs text-gray-400 mt-2">Loading preview…</p>
      </div>
    );
  }

  if (!presignedUrl) {
    return (
      <div className="text-center py-8">
        <AlertCircle size={48} className="text-gray-300 mx-auto mb-3" />
        <p className="text-sm text-gray-500">No download URL available</p>
      </div>
    );
  }

  if (useIframeFallback) {
    return (
      <div className="w-full">
        <iframe
          src={presignedUrl}
          className="w-full min-h-[500px] bg-white rounded-lg border border-gray-200"
          sandbox="allow-same-origin"
          title={`Preview of ${fileName}`}
        />
      </div>
    );
  }

  if (content === null) {
    return (
      <div className="text-center py-8">
        <AlertCircle size={48} className="text-gray-300 mx-auto mb-3" />
        <p className="text-sm text-gray-500">Could not load preview</p>
      </div>
    );
  }

  const lines = content.split("\n");
  const gutterWidth = String(lines.length).length;

  return (
    <div className="w-full">
      <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
        <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-b border-gray-200">
          <span className="text-xs font-medium text-gray-500 truncate">
            {fileName}
          </span>
          <span className="text-xs text-gray-400">
            {lines.length} line{lines.length !== 1 ? "s" : ""}
          </span>
        </div>
        <div className="overflow-auto max-h-[600px]">
          <table className="w-full border-collapse">
            <tbody>
              {lines.map((line, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td
                    className="sticky left-0 bg-gray-50 text-right select-none px-3 py-0 text-xs text-gray-400 font-mono border-r border-gray-200"
                    style={{ minWidth: `${gutterWidth + 2}ch` }}
                  >
                    {i + 1}
                  </td>
                  <td className="px-4 py-0 whitespace-pre font-mono text-sm text-gray-800 overflow-x-auto">
                    {line || "\u00A0"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {truncated && (
        <p className="text-xs text-amber-600 mt-2 text-center">
          File truncated — showing first {(MAX_PREVIEW_SIZE / 1000).toFixed(0)} KB. Download the file to see full contents.
        </p>
      )}
    </div>
  );
}

interface PdfFilePreviewProps {
  presignedUrl?: string;
}

export function PdfFilePreview({ presignedUrl }: PdfFilePreviewProps) {
  if (!presignedUrl) {
    return (
      <div className="text-center py-8">
        <File size={64} className="text-red-400 mx-auto mb-3" />
        <p className="text-sm text-gray-500">PDF preview not available</p>
      </div>
    );
  }

  return (
    <div className="w-full">
      <iframe
        src={presignedUrl}
        className="w-full rounded-lg border border-gray-200"
        style={{ minHeight: "600px" }}
        title="PDF preview"
      />
      <p className="text-xs text-gray-400 mt-2 text-center">
        If the preview doesn&apos;t load,{" "}
        <a
          href={presignedUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-otter-600 hover:underline"
        >
          open in a new tab
        </a>
      </p>
    </div>
  );
}

interface ImageFilePreviewProps {
  presignedUrl?: string;
  fileName: string;
}

export function ImageFilePreview({ presignedUrl, fileName }: ImageFilePreviewProps) {
  const [error, setError] = useState(false);

  useEffect(() => {
    setError(false);
  }, [presignedUrl]);

  if (!presignedUrl || error) {
    return (
      <div className="text-center py-8">
        <File size={64} className="text-gray-300 mx-auto mb-3" />
        <p className="text-sm text-gray-500">Image preview not available</p>
      </div>
    );
  }

  return (
    <img
      src={presignedUrl}
      alt={fileName}
      className="max-w-full max-h-[500px] rounded-lg shadow-sm"
      onError={() => setError(true)}
    />
  );
}

// ── Content-endpoint (same-origin, authenticated) preview renderers ─────────
// These fetch bytes from GET /files/{id}/content, which serves the correct
// Content-Type + inline disposition, avoiding the CORS/octet-stream issues of
// cross-origin presigned S3 URLs.

function ContentImagePreview({
  file,
  onDownload,
}: {
  file: PreviewFile;
  onDownload: () => void;
}) {
  const tooLarge = !!file.size && file.size > MAX_MEDIA_BYTES;
  const { url, status } = useContentBlobUrl(file.id, !tooLarge);
  if (tooLarge) return <PreviewTooLarge file={file} onDownload={onDownload} />;
  if (status === "loading") return <PreviewSpinner />;
  if (status === "error" || !url) return <PreviewError message="Image preview not available" />;
  return (
    <img
      src={url}
      alt={file.name}
      className="max-w-full max-h-[70vh] rounded-lg shadow-sm mx-auto"
    />
  );
}

function ContentPdfPreview({
  file,
  onDownload,
}: {
  file: PreviewFile;
  onDownload: () => void;
}) {
  const tooLarge = !!file.size && file.size > MAX_MEDIA_BYTES;
  const { url, status } = useContentBlobUrl(file.id, !tooLarge);
  if (tooLarge) return <PreviewTooLarge file={file} onDownload={onDownload} />;
  if (status === "loading") return <PreviewSpinner />;
  if (status === "error" || !url) return <PreviewError message="PDF preview not available" />;
  return (
    <div className="w-full">
      <iframe
        src={url}
        className="w-full rounded-lg border border-gray-200 bg-white"
        style={{ minHeight: "70vh" }}
        title={`Preview of ${file.name}`}
      />
    </div>
  );
}

function ContentMediaPreview({
  file,
  kind,
  onDownload,
}: {
  file: PreviewFile;
  kind: "video" | "audio";
  onDownload: () => void;
}) {
  const tooLarge = !!file.size && file.size > MAX_MEDIA_BYTES;
  const { url, status } = useContentBlobUrl(file.id, !tooLarge);
  if (tooLarge) return <PreviewTooLarge file={file} onDownload={onDownload} />;
  if (status === "loading") return <PreviewSpinner />;
  if (status === "error" || !url) {
    return <PreviewError message={`${kind === "video" ? "Video" : "Audio"} preview not available`} />;
  }
  if (kind === "video") {
    return (
      <video src={url} controls className="max-w-full max-h-[70vh] rounded-lg mx-auto">
        <track kind="captions" />
      </video>
    );
  }
  return (
    <div className="w-full flex flex-col items-center gap-4 py-8">
      <Music size={56} className="text-otter-500" />
      <audio src={url} controls className="w-full max-w-md">
        <track kind="captions" />
      </audio>
    </div>
  );
}

function ContentTextPreview({ file }: { file: PreviewFile }) {
  const [content, setContent] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [truncated, setTruncated] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setStatus("loading");
    filesApi
      .getContentText(file.id, MAX_PREVIEW_SIZE, { signal: controller.signal })
      .then(({ text, truncated: t }) => {
        if (cancelled) return;
        setContent(text);
        setTruncated(t);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (!cancelled && (err as Error)?.name !== "AbortError") setStatus("error");
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [file.id]);

  if (status === "loading") return <PreviewSpinner />;
  if (status === "error" || content === null) return <PreviewError message="Could not load preview" />;

  const lines = content.split("\n");
  const gutterWidth = String(lines.length).length;

  return (
    <div className="w-full">
      <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
        <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-b border-gray-200">
          <span className="text-xs font-medium text-gray-500 truncate">{file.name}</span>
          <span className="text-xs text-gray-400">
            {lines.length} line{lines.length !== 1 ? "s" : ""}
          </span>
        </div>
        <div className="overflow-auto max-h-[65vh]">
          <table className="w-full border-collapse">
            <tbody>
              {lines.map((line, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td
                    className="sticky left-0 bg-gray-50 text-right select-none px-3 py-0 text-xs text-gray-400 font-mono border-r border-gray-200"
                    style={{ minWidth: `${gutterWidth + 2}ch` }}
                  >
                    {i + 1}
                  </td>
                  <td className="px-4 py-0 whitespace-pre font-mono text-sm text-gray-800 overflow-x-auto">
                    {line || "\u00A0"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {truncated && (
        <p className="text-xs text-amber-600 mt-2 text-center">
          File truncated — showing first {(MAX_PREVIEW_SIZE / 1000).toFixed(0)} KB. Download the file to see full contents.
        </p>
      )}
    </div>
  );
}

// Renders an .xlsx/.xls workbook as a read-only table. Cells are rendered as
// escaped React text (never dangerouslySetInnerHTML), and XLSX.read parses only
// — it does not evaluate formulas or execute macros — so spreadsheet content
// cannot inject markup or scripts.
export function SpreadsheetFilePreview({ file }: { file: PreviewFile }) {
  const [status, setStatus] = useState<
    "loading" | "ready" | "error" | "too-large" | "empty"
  >("loading");
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [active, setActive] = useState(0);
  const [rows, setRows] = useState<string[][]>([]);
  const [truncated, setTruncated] = useState(false);
  const workbookRef = useRef<XLSXType.WorkBook | null>(null);
  const xlsxRef = useRef<typeof XLSXType | null>(null);

  useEffect(() => {
    if (file.size && file.size > MAX_SPREADSHEET_BYTES) {
      setStatus("too-large");
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setStatus("loading");
    workbookRef.current = null;

    // Lazy-load the (heavy) SheetJS parser only when a spreadsheet is opened.
    Promise.all([
      import("xlsx"),
      filesApi.getContentArrayBuffer(file.id, { signal: controller.signal }),
    ])
      .then(([xlsx, buf]) => {
        if (cancelled) return;
        xlsxRef.current = xlsx;
        // read() parses only — no formula evaluation or macro execution.
        const wb = xlsx.read(buf, { type: "array" });
        workbookRef.current = wb;
        if (!wb.SheetNames.length) {
          setStatus("empty");
          return;
        }
        setSheetNames(wb.SheetNames);
        setActive(0);
      })
      .catch((err: unknown) => {
        if (!cancelled && (err as Error)?.name !== "AbortError") setStatus("error");
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [file.id, file.size]);

  useEffect(() => {
    const wb = workbookRef.current;
    const xlsx = xlsxRef.current;
    if (!wb || !xlsx || sheetNames.length === 0) return;
    const ws = wb.Sheets[sheetNames[active]];
    const data = xlsx.utils.sheet_to_json<unknown[]>(ws, {
      header: 1,
      raw: false,
      defval: "",
      blankrows: false,
    });
    const rowTrunc = data.length > MAX_SHEET_ROWS;
    let colTrunc = false;
    const limited = data.slice(0, MAX_SHEET_ROWS).map((r) => {
      const cells = (r as unknown[]).map((c) => (c == null ? "" : String(c)));
      if (cells.length > MAX_SHEET_COLS) colTrunc = true;
      return cells.slice(0, MAX_SHEET_COLS);
    });
    setRows(limited);
    setTruncated(rowTrunc || colTrunc);
    setStatus("ready");
  }, [active, sheetNames]);

  if (status === "loading") return <PreviewSpinner label="Parsing spreadsheet…" />;
  if (status === "too-large") {
    return (
      <PreviewError message="This spreadsheet is too large to preview. Please download it to view." />
    );
  }
  if (status === "error") return <PreviewError message="Could not load spreadsheet preview" />;
  if (status === "empty") return <PreviewError message="This spreadsheet has no sheets" />;

  return (
    <div className="w-full">
      {sheetNames.length > 1 && (
        <div className="flex items-center gap-1 mb-2 overflow-x-auto">
          {sheetNames.map((name, i) => (
            <button
              key={name}
              onClick={() => setActive(i)}
              className={`px-3 py-1 text-xs rounded-t-md whitespace-nowrap border-b-2 ${
                i === active
                  ? "border-otter-600 text-otter-700 font-medium bg-white"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              }`}
            >
              {name}
            </button>
          ))}
        </div>
      )}
      <div className="rounded-lg border border-gray-200 bg-white overflow-auto max-h-[65vh]">
        <table className="border-collapse text-sm">
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className={r === 0 ? "bg-gray-50 font-medium" : "hover:bg-gray-50"}>
                <td className="sticky left-0 bg-gray-50 text-right select-none px-2 py-1 text-xs text-gray-400 font-mono border-r border-b border-gray-200">
                  {r + 1}
                </td>
                {row.map((cell, c) => (
                  <td
                    key={c}
                    className="px-3 py-1 border-r border-b border-gray-100 text-gray-800 whitespace-nowrap max-w-[240px] truncate"
                    title={cell}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {truncated && (
        <p className="text-xs text-amber-600 mt-2 text-center">
          Large sheet — showing first {MAX_SHEET_ROWS} rows / {MAX_SHEET_COLS} columns. Download the file for full contents.
        </p>
      )}
    </div>
  );
}

function GenericFilePreview({ file, onDownload }: { file: PreviewFile; onDownload: () => void }) {
  return (
    <div className="text-center py-10">
      <File size={64} className="text-gray-300 mx-auto mb-3" />
      <p className="text-sm text-gray-600 font-medium">{file.name}</p>
      <p className="text-xs text-gray-400 mt-1">
        {file.mimeType || "unknown type"} · {formatFileSize(file.size)}
      </p>
      <p className="text-sm text-gray-500 mt-3">
        Preview isn&apos;t available for this file type.
      </p>
      <button
        onClick={onDownload}
        className="mt-4 inline-flex items-center gap-2 px-4 py-2 text-sm text-white bg-otter-600 rounded-lg hover:bg-otter-700 transition"
      >
        <Download size={16} />
        Download
      </button>
    </div>
  );
}

/** Dispatches to the correct renderer based on the file's preview kind. */
export function FilePreview({
  file,
  onDownload,
}: {
  file: PreviewFile;
  onDownload: () => void;
}) {
  const kind = getPreviewKind(file.mimeType, file.name);
  switch (kind) {
    case "image":
      return <ContentImagePreview file={file} onDownload={onDownload} />;
    case "pdf":
      return <ContentPdfPreview file={file} onDownload={onDownload} />;
    case "video":
      return <ContentMediaPreview file={file} kind="video" onDownload={onDownload} />;
    case "audio":
      return <ContentMediaPreview file={file} kind="audio" onDownload={onDownload} />;
    case "text":
      return <ContentTextPreview file={file} />;
    case "spreadsheet":
      return <SpreadsheetFilePreview file={file} />;
    default:
      return <GenericFilePreview file={file} onDownload={onDownload} />;
  }
}

/** Modal/lightbox that previews a file inline from the Files list. */
export function FilePreviewModal({
  file,
  onClose,
}: {
  file: PreviewFile;
  onClose: () => void;
}) {
  const [isDownloading, setIsDownloading] = useState(false);

  const handleDownload = useCallback(async () => {
    setIsDownloading(true);
    try {
      const url = await filesApi.getDownloadUrl(file.id);
      const a = document.createElement("a");
      a.href = url;
      a.download = file.name;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch {
      // best-effort; the preview itself still works
    } finally {
      setIsDownloading(false);
    }
  }, [file.id, file.name]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Preview of ${file.name}`}
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-gray-900 truncate">{file.name}</h2>
            <p className="text-xs text-gray-400">{formatFileSize(file.size)}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleDownload}
              disabled={isDownloading}
              className="flex items-center gap-2 px-3 py-1.5 text-sm text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition disabled:opacity-50"
            >
              {isDownloading ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
              Download
            </button>
            <button
              onClick={onClose}
              aria-label="Close preview"
              className="p-2 rounded-lg hover:bg-gray-100 text-gray-500"
            >
              <X size={18} />
            </button>
          </div>
        </div>
        <div className="p-5 overflow-auto bg-gray-50 flex-1 flex items-center justify-center">
          <div className="w-full">
            <FilePreview file={file} onDownload={handleDownload} />
          </div>
        </div>
      </div>
    </div>
  );
}
