import { useState, useEffect, useCallback } from "react";
import { File, AlertCircle, Download, RefreshCw } from "lucide-react";
import { capRows, parseCsv, sanitizeDocHtml } from "@/lib/preview";
import { formatFileSize } from "@/lib/utils";

const MAX_PREVIEW_SIZE = 500_000; // 500 KB — truncate beyond this

// ── Shared byte fetching ────────────────────────────────────────────────
// S3/LocalStack serves seeded objects as binary/octet-stream, so previews
// fetch raw bytes and type them client-side from the stored mime_type.

interface BytesState {
  bytes: ArrayBuffer | null;
  loading: boolean;
  error: boolean;
}

function useFileBytes(presignedUrl?: string) {
  const [state, setState] = useState<BytesState>({ bytes: null, loading: true, error: false });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!presignedUrl) {
      setState({ bytes: null, loading: false, error: false });
      return;
    }
    let cancelled = false;
    setState({ bytes: null, loading: true, error: false });
    fetch(presignedUrl)
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const buf = await res.arrayBuffer();
        if (!cancelled) setState({ bytes: buf, loading: false, error: false });
      })
      .catch(() => {
        if (!cancelled) setState({ bytes: null, loading: false, error: true });
      });
    return () => {
      cancelled = true;
    };
  }, [presignedUrl, attempt]);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);
  return { ...state, retry };
}

function PreviewSpinner() {
  return (
    <div className="w-full text-center py-8">
      <div className="w-6 h-6 border-2 border-otter-600 border-t-transparent rounded-full animate-spin mx-auto" />
      <p className="text-xs text-gray-400 mt-2">Loading preview…</p>
    </div>
  );
}

function NoUrlMessage() {
  return (
    <div className="text-center py-8">
      <AlertCircle size={48} className="text-gray-300 mx-auto mb-3" />
      <p className="text-sm text-gray-500">No download URL available</p>
    </div>
  );
}

function FetchErrorMessage({ onRetry }: Readonly<{ onRetry: () => void }>) {
  return (
    <div className="text-center py-8">
      <AlertCircle size={48} className="text-gray-300 mx-auto mb-3" />
      <p className="text-sm text-gray-500">Could not load preview</p>
      <button
        onClick={onRetry}
        className="mt-3 inline-flex items-center gap-2 px-3 py-1.5 text-sm text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition"
      >
        <RefreshCw size={14} />
        Retry
      </button>
    </div>
  );
}

// ── Text / code ─────────────────────────────────────────────────────────

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

  if (loading) return <PreviewSpinner />;
  if (!presignedUrl) return <NoUrlMessage />;

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

// ── PDF ─────────────────────────────────────────────────────────────────

interface PdfFilePreviewProps {
  presignedUrl?: string;
}

export function PdfFilePreview({ presignedUrl }: PdfFilePreviewProps) {
  const { bytes, loading, error, retry } = useFileBytes(presignedUrl);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!bytes) {
      setBlobUrl(null);
      return;
    }
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    setBlobUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [bytes]);

  if (!presignedUrl) return <NoUrlMessage />;
  if (loading || (bytes && !blobUrl)) return <PreviewSpinner />;
  if (error || !blobUrl) return <FetchErrorMessage onRetry={retry} />;

  return (
    <div className="w-full">
      <iframe
        src={blobUrl}
        className="w-full rounded-lg border border-gray-200"
        style={{ minHeight: "600px" }}
        title="PDF preview"
      />
      <p className="text-xs text-gray-400 mt-2 text-center">
        If the preview doesn&apos;t load,{" "}
        <a
          href={blobUrl}
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

// ── Image ───────────────────────────────────────────────────────────────

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

// ── Audio ───────────────────────────────────────────────────────────────

interface AudioFilePreviewProps {
  presignedUrl?: string;
  fileName: string;
}

export function AudioFilePreview({ presignedUrl, fileName }: AudioFilePreviewProps) {
  if (!presignedUrl) return <NoUrlMessage />;
  return (
    <div className="w-full max-w-lg">
      <p className="text-sm font-medium text-gray-700 mb-3 text-center truncate">{fileName}</p>
      <audio src={presignedUrl} controls className="w-full">
        <track kind="captions" />
      </audio>
    </div>
  );
}

// ── Tabular (CSV + spreadsheets) ────────────────────────────────────────

export interface PreviewSheet {
  name: string;
  rows: string[][];
}

function SheetTable({ sheets, fileName }: Readonly<{ sheets: PreviewSheet[]; fileName: string }>) {
  const [activeIdx, setActiveIdx] = useState(0);
  const sheet = sheets[Math.min(activeIdx, sheets.length - 1)];
  const { rows, truncated } = capRows(sheet.rows);
  const [header, ...body] = rows.length > 0 ? rows : [[]];

  return (
    <div className="w-full">
      <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
        <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-b border-gray-200">
          <span className="text-xs font-medium text-gray-500 truncate">{fileName}</span>
          <span className="text-xs text-gray-400">
            {sheet.rows.length} row{sheet.rows.length !== 1 ? "s" : ""}
          </span>
        </div>
        {sheets.length > 1 && (
          <div className="flex items-center gap-1 px-2 py-1.5 bg-gray-50 border-b border-gray-200 overflow-x-auto">
            {sheets.map((s, i) => (
              <button
                key={s.name}
                onClick={() => setActiveIdx(i)}
                className={
                  i === activeIdx
                    ? "px-3 py-1 text-xs font-medium rounded-md bg-white border border-gray-300 text-otter-700 shadow-sm whitespace-nowrap"
                    : "px-3 py-1 text-xs rounded-md text-gray-500 hover:bg-gray-100 whitespace-nowrap"
                }
              >
                {s.name}
              </button>
            ))}
          </div>
        )}
        <div className="overflow-auto max-h-[600px]">
          {rows.length === 0 || header.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-8">This sheet is empty</p>
          ) : (
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr>
                  {header.map((cell, i) => (
                    <th
                      key={i}
                      className="sticky top-0 bg-gray-50 px-3 py-2 text-left text-xs font-semibold text-gray-600 border-b border-r border-gray-200 whitespace-nowrap"
                    >
                      {cell}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {body.map((row, ri) => (
                  <tr key={ri} className="hover:bg-gray-50">
                    {header.map((_, ci) => (
                      <td
                        key={ci}
                        className="px-3 py-1.5 text-gray-800 border-b border-r border-gray-100 whitespace-nowrap"
                      >
                        {row[ci] ?? ""}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {truncated && (
        <p className="text-xs text-amber-600 mt-2 text-center">
          Table truncated — showing the first {rows.length} rows. Download the file to see full contents.
        </p>
      )}
    </div>
  );
}

interface CsvFilePreviewProps {
  presignedUrl?: string;
  fileName: string;
}

export function CsvFilePreview({ presignedUrl, fileName }: CsvFilePreviewProps) {
  const { bytes, loading, error, retry } = useFileBytes(presignedUrl);

  if (!presignedUrl) return <NoUrlMessage />;
  if (loading) return <PreviewSpinner />;
  if (error || !bytes) return <FetchErrorMessage onRetry={retry} />;

  const rows = parseCsv(new TextDecoder().decode(bytes));
  return <SheetTable sheets={[{ name: fileName, rows }]} fileName={fileName} />;
}

interface SpreadsheetFilePreviewProps {
  presignedUrl?: string;
  fileName: string;
  fallback: React.ReactNode;
}

export function SpreadsheetFilePreview({ presignedUrl, fileName, fallback }: SpreadsheetFilePreviewProps) {
  const { bytes, loading, error, retry } = useFileBytes(presignedUrl);
  const [sheets, setSheets] = useState<PreviewSheet[] | null>(null);
  const [parseFailed, setParseFailed] = useState(false);

  useEffect(() => {
    if (!bytes) {
      setSheets(null);
      setParseFailed(false);
      return;
    }
    let cancelled = false;
    import("xlsx")
      .then((XLSX) => {
        const workbook = XLSX.read(new Uint8Array(bytes), { type: "array" });
        const parsed: PreviewSheet[] = workbook.SheetNames.map((name) => ({
          name,
          rows: XLSX.utils.sheet_to_json<string[]>(workbook.Sheets[name], {
            header: 1,
            raw: false,
            defval: "",
          }).map((row) => row.map((cell) => String(cell ?? ""))),
        }));
        if (!cancelled) setSheets(parsed);
      })
      .catch(() => {
        if (!cancelled) setParseFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [bytes]);

  if (!presignedUrl) return <NoUrlMessage />;
  if (parseFailed) return <>{fallback}</>;
  if (error) return <FetchErrorMessage onRetry={retry} />;
  if (loading || !sheets) return <PreviewSpinner />;

  return <SheetTable sheets={sheets} fileName={fileName} />;
}

// ── DOCX ────────────────────────────────────────────────────────────────

interface DocxFilePreviewProps {
  presignedUrl?: string;
  fileName: string;
  fallback: React.ReactNode;
}

export function DocxFilePreview({ presignedUrl, fileName, fallback }: DocxFilePreviewProps) {
  const { bytes, loading, error, retry } = useFileBytes(presignedUrl);
  const [html, setHtml] = useState<string | null>(null);
  const [parseFailed, setParseFailed] = useState(false);

  useEffect(() => {
    if (!bytes) {
      setHtml(null);
      setParseFailed(false);
      return;
    }
    let cancelled = false;
    import("mammoth/mammoth.browser")
      .then((mammoth) => mammoth.convertToHtml({ arrayBuffer: bytes }))
      .then((result) => {
        if (!cancelled) setHtml(sanitizeDocHtml(result.value));
      })
      .catch(() => {
        if (!cancelled) setParseFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [bytes]);

  if (!presignedUrl) return <NoUrlMessage />;
  if (parseFailed) return <>{fallback}</>;
  if (error) return <FetchErrorMessage onRetry={retry} />;
  if (loading || html === null) return <PreviewSpinner />;

  return (
    <div className="w-full">
      <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
        <div className="px-4 py-2 bg-gray-50 border-b border-gray-200">
          <span className="text-xs font-medium text-gray-500 truncate">{fileName}</span>
        </div>
        <div
          className="prose prose-sm max-w-none px-6 py-5 overflow-auto max-h-[600px] [&_h1]:text-xl [&_h1]:font-bold [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:font-semibold [&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_table]:border-collapse [&_td]:border [&_td]:border-gray-200 [&_td]:px-2 [&_td]:py-1"
          // Content is mammoth-converted docx passed through sanitizeDocHtml
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
    </div>
  );
}

// ── Generic fallback ────────────────────────────────────────────────────

interface FallbackFilePreviewProps {
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  note?: string;
  onDownload?: () => void;
}

export function FallbackFilePreview({
  fileName,
  mimeType,
  sizeBytes,
  note,
  onDownload,
}: FallbackFilePreviewProps) {
  return (
    <div className="text-center py-8 max-w-sm mx-auto">
      <File size={64} className="text-gray-300 mx-auto mb-3" />
      <p className="text-sm font-medium text-gray-700 truncate">{fileName}</p>
      <p className="text-xs text-gray-500 mt-1">
        {mimeType || "Unknown type"} &middot; {formatFileSize(sizeBytes)}
      </p>
      <p className="text-sm text-gray-500 mt-3">
        {note ?? "Inline preview isn't available for this file type."}
      </p>
      {onDownload && (
        <button
          onClick={onDownload}
          className="mt-4 inline-flex items-center gap-2 px-3 py-2 text-sm text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition"
        >
          <Download size={16} />
          Download
        </button>
      )}
    </div>
  );
}
