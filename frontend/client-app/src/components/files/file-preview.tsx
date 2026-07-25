import { useState, useEffect } from "react";
import { File, AlertCircle, ExternalLink, FileText, Film, Music } from "lucide-react";
import { getPreviewKind, parseDelimited, getFileExtension } from "@/lib/file-preview";

const MAX_PREVIEW_SIZE = 500_000; // 500 KB — truncate beyond this

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
    return <PreviewSpinner />;
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

interface CsvFilePreviewProps {
  presignedUrl?: string;
  fileName: string;
}

export function CsvFilePreview({ presignedUrl, fileName }: CsvFilePreviewProps) {
  const [rows, setRows] = useState<string[][] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [truncated, setTruncated] = useState(false);

  useEffect(() => {
    if (!presignedUrl) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setRows(null);
    setError(false);

    const delimiter = getFileExtension(fileName) === "tsv" ? "\t" : ",";

    fetch(presignedUrl, { headers: { Range: `bytes=0-${MAX_PREVIEW_SIZE - 1}` } })
      .then(async (res) => {
        if (!res.ok && res.status !== 206) throw new Error(`HTTP ${res.status}`);
        const text = await res.text();
        if (cancelled) return;
        setRows(parseDelimited(text, delimiter));
        setTruncated(res.status === 206);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [presignedUrl, fileName]);

  if (loading) return <PreviewSpinner />;

  // Fall back to a plain text view if the delimited parse produced nothing useful.
  if (error || !rows || rows.length === 0) {
    return <TextFilePreview presignedUrl={presignedUrl} fileName={fileName} />;
  }

  const [header, ...body] = rows;

  return (
    <div className="w-full">
      <div className="rounded-lg border border-gray-200 bg-white overflow-auto max-h-[600px]">
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0">
            <tr className="bg-gray-50">
              {header.map((cell, i) => (
                <th
                  key={i}
                  className="text-left font-medium text-gray-600 px-3 py-2 border-b border-gray-200 whitespace-nowrap"
                >
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, r) => (
              <tr key={r} className="hover:bg-gray-50">
                {header.map((_, c) => (
                  <td key={c} className="px-3 py-1.5 border-b border-gray-100 text-gray-700 whitespace-nowrap">
                    {row[c] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {truncated && (
        <p className="text-xs text-amber-600 mt-2 text-center">
          Showing first {(MAX_PREVIEW_SIZE / 1000).toFixed(0)} KB. Download the file to see all rows.
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

interface AudioFilePreviewProps {
  presignedUrl?: string;
  fileName: string;
}

export function AudioFilePreview({ presignedUrl, fileName }: AudioFilePreviewProps) {
  if (!presignedUrl) {
    return (
      <div className="text-center py-8">
        <Music size={64} className="text-gray-300 mx-auto mb-3" />
        <p className="text-sm text-gray-500">Audio preview not available</p>
      </div>
    );
  }

  return (
    <div className="w-full max-w-md text-center">
      <div className="w-20 h-20 rounded-2xl bg-otter-50 flex items-center justify-center mx-auto mb-4">
        <Music size={36} className="text-otter-600" />
      </div>
      <p className="text-sm font-medium text-gray-700 truncate mb-3">{fileName}</p>
      <audio src={presignedUrl} controls className="w-full">
        <track kind="captions" />
      </audio>
    </div>
  );
}

interface VideoFilePreviewProps {
  presignedUrl?: string;
}

export function VideoFilePreview({ presignedUrl }: VideoFilePreviewProps) {
  if (!presignedUrl) {
    return (
      <div className="text-center py-8">
        <Film size={64} className="text-gray-300 mx-auto mb-3" />
        <p className="text-sm text-gray-500">Video preview not available</p>
      </div>
    );
  }

  return (
    <video src={presignedUrl} controls className="max-w-full max-h-[500px] rounded-lg">
      <track kind="captions" />
    </video>
  );
}

interface GenericFilePreviewProps {
  presignedUrl?: string;
  fileName: string;
  mimeType: string;
  isOffice?: boolean;
}

export function GenericFilePreview({ presignedUrl, fileName, mimeType, isOffice }: GenericFilePreviewProps) {
  const Icon = isOffice ? FileText : File;
  return (
    <div className="text-center py-6 max-w-sm">
      <div className="w-20 h-20 rounded-2xl bg-gray-100 flex items-center justify-center mx-auto mb-4">
        <Icon size={36} className="text-gray-400" />
      </div>
      <p className="text-sm font-medium text-gray-700 truncate mb-1">{fileName}</p>
      <p className="text-xs text-gray-400 mb-4">
        {isOffice
          ? "Inline preview isn't available for this document type."
          : "No inline preview available for this file type."}
      </p>
      {presignedUrl && (
        <a
          href={presignedUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 px-4 py-2 text-sm text-otter-700 bg-otter-50 rounded-lg hover:bg-otter-100 transition"
        >
          <ExternalLink size={16} />
          Open in new tab
        </a>
      )}
      <p className="sr-only">{mimeType}</p>
    </div>
  );
}

function PreviewSpinner() {
  return (
    <div className="w-full text-center py-8">
      <div className="w-6 h-6 border-2 border-otter-600 border-t-transparent rounded-full animate-spin mx-auto" />
      <p className="text-xs text-gray-400 mt-2">Loading preview…</p>
    </div>
  );
}

export interface FilePreviewProps {
  fileName: string;
  mimeType: string;
  presignedUrl?: string;
  isUrlLoading?: boolean;
}

/**
 * Renders the appropriate inline preview for a file based on its type,
 * gracefully falling back for unsupported types. Shared by the file-detail
 * page and the quick-preview modal so both stay in sync.
 */
export function FilePreview({ fileName, mimeType, presignedUrl, isUrlLoading }: FilePreviewProps) {
  const kind = getPreviewKind(mimeType, fileName);

  if (isUrlLoading) {
    return <PreviewSpinner />;
  }

  switch (kind) {
    case "image":
      return <ImageFilePreview presignedUrl={presignedUrl} fileName={fileName} />;
    case "video":
      return <VideoFilePreview presignedUrl={presignedUrl} />;
    case "audio":
      return <AudioFilePreview presignedUrl={presignedUrl} fileName={fileName} />;
    case "pdf":
      return <PdfFilePreview presignedUrl={presignedUrl} />;
    case "csv":
      return <CsvFilePreview presignedUrl={presignedUrl} fileName={fileName} />;
    case "text":
      return <TextFilePreview presignedUrl={presignedUrl} fileName={fileName} />;
    case "office":
      return <GenericFilePreview presignedUrl={presignedUrl} fileName={fileName} mimeType={mimeType} isOffice />;
    default:
      return <GenericFilePreview presignedUrl={presignedUrl} fileName={fileName} mimeType={mimeType} />;
  }
}
