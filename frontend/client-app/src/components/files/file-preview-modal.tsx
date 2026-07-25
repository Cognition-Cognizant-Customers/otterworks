import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { X, Download, ExternalLink, AlertCircle } from "lucide-react";
import { FilePreview } from "@/components/files/file-preview";
import { filesApi } from "@/lib/api";
import { formatFileSize } from "@/lib/utils";
import type { FileItem } from "@/types";

interface FilePreviewModalProps {
  file: FileItem;
  onClose: () => void;
  onDownload?: (id: string, name: string) => void;
}

export function FilePreviewModal({ file, onClose, onDownload }: FilePreviewModalProps) {
  const {
    data: presignedUrl,
    isLoading: isUrlLoading,
    isError,
  } = useQuery({
    queryKey: ["files", file.id, "download-url"],
    queryFn: () => filesApi.getDownloadUrl(file.id),
    staleTime: 30 * 60 * 1000,
  });

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Preview of ${file.name}`}
          className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between gap-4 px-5 py-4 border-b border-gray-200">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-gray-900 truncate">{file.name}</h2>
              <p className="text-xs text-gray-400">
                {file.mimeType || "Unknown type"} · {formatFileSize(file.size)}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              {presignedUrl && (
                <a
                  href={presignedUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="p-2 rounded-lg hover:bg-gray-100 text-gray-500"
                  aria-label="Open in new tab"
                  title="Open in new tab"
                >
                  <ExternalLink size={18} />
                </a>
              )}
              <button
                onClick={() => onDownload?.(file.id, file.name)}
                className="p-2 rounded-lg hover:bg-gray-100 text-gray-500"
                aria-label="Download"
                title="Download"
              >
                <Download size={18} />
              </button>
              <button
                onClick={onClose}
                className="p-2 rounded-lg hover:bg-gray-100 text-gray-500"
                aria-label="Close preview"
              >
                <X size={18} />
              </button>
            </div>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-auto p-6 flex items-center justify-center bg-gray-50 min-h-[300px]">
            {isError ? (
              <div className="text-center py-8">
                <AlertCircle size={48} className="text-gray-300 mx-auto mb-3" />
                <p className="text-sm text-gray-500">Couldn&apos;t load a preview for this file</p>
              </div>
            ) : (
              <FilePreview
                fileName={file.name}
                mimeType={file.mimeType}
                presignedUrl={presignedUrl}
                isUrlLoading={isUrlLoading}
              />
            )}
          </div>
        </div>
      </div>
    </>
  );
}
