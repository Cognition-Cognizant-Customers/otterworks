import { useCallback, useState, useRef, useEffect, useImperativeHandle, forwardRef } from "react";
import type { Ref } from "react";
import { useDropzone } from "react-dropzone";
import { Upload, X, FileIcon, CheckCircle2, AlertCircle, RotateCcw } from "lucide-react";
import { cn, formatFileSize } from "@/lib/utils";

interface FileUploadDropzoneProps {
  uploadFile: (
    file: File,
    options: { onProgress: (percent: number) => void; signal: AbortSignal },
  ) => Promise<void>;
  onUploadComplete?: () => void;
  onDismiss?: () => void;
  className?: string;
}

export interface FileUploadDropzoneHandle {
  addFiles: (files: File[]) => void;
}

interface UploadingFile {
  id: string;
  file: File;
  progress: number;
  status: "uploading" | "done" | "error";
  error?: string;
  abortController?: AbortController;
}

let fileIdCounter = 0;

const patchEntry =
  (id: string, patch: Partial<UploadingFile>) =>
  (prev: UploadingFile[]): UploadingFile[] =>
    prev.map((f) => (f.id === id ? { ...f, ...patch } : f));

const removeEntry =
  (id: string) =>
  (prev: UploadingFile[]): UploadingFile[] =>
    prev.filter((f) => f.id !== id);

export const FileUploadDropzone = forwardRef(function FileUploadDropzone(
  { uploadFile, onUploadComplete, onDismiss, className }: FileUploadDropzoneProps,
  ref: Ref<FileUploadDropzoneHandle>,
) {
  const [uploadingFiles, setUploadingFiles] = useState<UploadingFile[]>([]);
  const [dismissing, setDismissing] = useState(false);
  const dismissTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onDismissRef = useRef(onDismiss);

  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  useEffect(() => {
    if (uploadingFiles.length === 0) {
      setDismissing(false);
      return;
    }
    const allDone = uploadingFiles.every((f) => f.status === "done");
    const hasUploading = uploadingFiles.some((f) => f.status === "uploading");

    if (allDone && !hasUploading) {
      setDismissing(true);
      dismissTimerRef.current = setTimeout(() => {
        setUploadingFiles([]);
        setDismissing(false);
        onDismissRef.current?.();
      }, 3000);
    } else {
      setDismissing(false);
      if (dismissTimerRef.current) {
        clearTimeout(dismissTimerRef.current);
        dismissTimerRef.current = null;
      }
    }

    return () => {
      if (dismissTimerRef.current) {
        clearTimeout(dismissTimerRef.current);
      }
    };
  }, [uploadingFiles]);

  const startUpload = useCallback(
    (entry: UploadingFile) => {
      const abortController = new AbortController();

      setUploadingFiles(
        patchEntry(entry.id, {
          status: "uploading",
          progress: 0,
          error: undefined,
          abortController,
        }),
      );

      uploadFile(entry.file, {
        onProgress: (percent) => setUploadingFiles(patchEntry(entry.id, { progress: percent })),
        signal: abortController.signal,
      })
        .then(() => {
          setUploadingFiles(
            patchEntry(entry.id, { status: "done", progress: 100, abortController: undefined }),
          );
          onUploadComplete?.();
        })
        .catch(() => {
          if (abortController.signal.aborted) {
            setUploadingFiles(removeEntry(entry.id));
          } else {
            setUploadingFiles(
              patchEntry(entry.id, {
                status: "error",
                error: "Upload failed",
                abortController: undefined,
              }),
            );
          }
        });
    },
    [uploadFile, onUploadComplete],
  );

  const addFiles = useCallback(
    (files: File[]) => {
      const newFiles: UploadingFile[] = files.map((file) => ({
        id: `upload-${++fileIdCounter}`,
        file,
        progress: 0,
        status: "uploading" as const,
      }));
      setUploadingFiles((prev) => [...prev, ...newFiles]);
      newFiles.forEach((entry) => startUpload(entry));
    },
    [startUpload],
  );

  useImperativeHandle(ref, () => ({ addFiles }), [addFiles]);

  const cancelUpload = (id: string) => {
    const entry = uploadingFiles.find((f) => f.id === id);
    entry?.abortController?.abort();
  };

  const retryUpload = (id: string) => {
    const entry = uploadingFiles.find((f) => f.id === id);
    if (entry) startUpload(entry);
  };

  const clearCompleted = () => {
    setUploadingFiles((prev) => prev.filter((f) => f.status !== "done"));
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: addFiles,
    multiple: true,
  });

  return (
    <div className={className}>
      <div
        {...getRootProps()}
        className={cn(
          "border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition",
          isDragActive
            ? "border-otter-500 bg-otter-50"
            : "border-gray-300 hover:border-otter-400 hover:bg-gray-50",
        )}
      >
        <input {...getInputProps()} />
        <Upload
          size={32}
          className={cn(
            "mx-auto mb-3",
            isDragActive ? "text-otter-600" : "text-gray-400",
          )}
        />
        {isDragActive ? (
          <p className="text-sm text-otter-600 font-medium">Drop files here</p>
        ) : (
          <>
            <p className="text-sm text-gray-600 font-medium">
              Drag & drop files here, or click to browse
            </p>
            <p className="text-xs text-gray-400 mt-1">
              Any file type, up to 100MB per file
            </p>
          </>
        )}
      </div>

      {uploadingFiles.length > 0 && (
        <div className="mt-4 space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium text-gray-700">
              {dismissing ? (
                <span className="flex items-center gap-1.5 text-green-600">
                  <CheckCircle2 size={14} />
                  Upload complete — closing shortly
                </span>
              ) : (
                "Uploads"
              )}
            </p>
            {!dismissing && (
              <button
                onClick={clearCompleted}
                className="text-xs text-gray-500 hover:text-gray-700"
              >
                Clear completed
              </button>
            )}
          </div>
          {uploadingFiles.map((item) => (
            <div
              key={item.id}
              className="flex items-center gap-3 p-2 bg-white rounded-lg border border-gray-200"
            >
              <FileIcon size={16} className="text-gray-400 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-gray-700 truncate">{item.file.name}</p>
                <div className="flex items-center gap-2">
                  <p className="text-xs text-gray-400">{formatFileSize(item.file.size)}</p>
                  {item.status === "uploading" && (
                    <p className="text-xs text-otter-600 font-medium">{item.progress}%</p>
                  )}
                </div>
                {item.status === "uploading" && (
                  <div className="mt-1 h-1.5 w-full bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-otter-500 rounded-full transition-all duration-300"
                      style={{ width: `${item.progress}%` }}
                    />
                  </div>
                )}
                {item.status === "error" && (
                  <p className="text-xs text-red-500 mt-0.5">{item.error}</p>
                )}
              </div>
              {item.status === "done" && (
                <CheckCircle2 size={16} className="text-green-500 flex-shrink-0" />
              )}
              {item.status === "error" && (
                <div className="flex items-center gap-1 flex-shrink-0">
                  <button
                    onClick={() => retryUpload(item.id)}
                    className="p-1 text-gray-400 hover:text-otter-600 transition"
                    title="Retry upload"
                  >
                    <RotateCcw size={14} />
                  </button>
                  <AlertCircle size={16} className="text-red-500" />
                </div>
              )}
              {item.status === "uploading" && (
                <button
                  onClick={() => cancelUpload(item.id)}
                  className="p-1 text-gray-400 hover:text-red-500 transition flex-shrink-0"
                  title="Cancel upload"
                >
                  <X size={14} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
});
