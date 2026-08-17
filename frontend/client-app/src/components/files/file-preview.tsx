import { useState, useEffect } from "react";
import { File, AlertCircle } from "lucide-react";

const MAX_PREVIEW_SIZE = 500_000; // 500 KB — truncate beyond this

interface TextFilePreviewProps {
  presignedUrl?: string;
  fileName: string;
}

export function TextFilePreview({ presignedUrl, fileName }: Readonly<TextFilePreviewProps>) {
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

export function PdfFilePreview({ presignedUrl }: Readonly<PdfFilePreviewProps>) {
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

export function ImageFilePreview({ presignedUrl, fileName }: Readonly<ImageFilePreviewProps>) {
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
