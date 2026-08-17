import { useState, useCallback, useEffect, useRef, Suspense } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { useDropzone } from "react-dropzone";
import {
  LayoutGrid,
  List,
  FolderPlus,
  Upload,
  FolderOpen,
  ArrowUpDown,
  ChevronUp,
  ChevronDown,
  Trash2,
  X,
  CheckSquare,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Breadcrumb, type BreadcrumbItem } from "@/components/layout/breadcrumb";
import { FileCard } from "@/components/files/file-card";
import { FolderCard } from "@/components/files/folder-card";
import { FileUploadDropzone } from "@/components/files/file-upload-dropzone";
import type { FileUploadDropzoneHandle } from "@/components/files/file-upload-dropzone";
import { ShareDialog } from "@/components/files/share-dialog";
import { PageLoader } from "@/components/ui/loading-spinner";
import { FileGridSkeleton, FileListSkeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { filesApi } from "@/lib/api";
import { useUIStore } from "@/stores/ui-store";
import { cn } from "@/lib/utils";
import type { ViewMode, SortField } from "@/types";

// Run a delete operation over a list of ids, tallying successes and failures
// so a single failure doesn't abort the rest of a bulk action.
async function runDeletions(
  ids: string[],
  remove: (id: string) => Promise<unknown>
): Promise<{ succeeded: number; failed: number }> {
  let succeeded = 0;
  let failed = 0;
  for (const id of ids) {
    try {
      await remove(id);
      succeeded++;
    } catch {
      failed++;
    }
  }
  return { succeeded, failed };
}

function FileBrowserContent() {
  const [searchParams] = useSearchParams();
  const folderId = searchParams.get("folder");
  const queryClient = useQueryClient();
  const { viewMode, setViewMode, sortConfig, setSortConfig } = useUIStore();
  const [showUpload, setShowUpload] = useState(false);
  const uploadDropzoneRef = useRef<FileUploadDropzoneHandle>(null);
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [shareFileId, setShareFileId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [selectionActive, setSelectionActive] = useState(false);

  useEffect(() => {
    setSelectedIds(new Set());
    setSelectionActive(false);
  }, [folderId]);

  const { data, isLoading: filesLoading } = useQuery({
    queryKey: ["files", "list", folderId],
    queryFn: () => filesApi.list(folderId),
  });

  const { data: folderItems, isLoading: foldersLoading } = useQuery({
    queryKey: ["folders", "list", folderId],
    queryFn: () => filesApi.listFolders(folderId),
  });

  const { data: currentFolder } = useQuery({
    queryKey: ["folders", "detail", folderId],
    queryFn: () => filesApi.getFolder(folderId!),
    enabled: !!folderId,
  });

  const isLoading = filesLoading || foldersLoading;

  const deleteMutation = useMutation({
    mutationFn: filesApi.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["files"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["storage", "usage"] });
      toast.success("File moved to trash");
    },
    onError: () => toast.error("Failed to move file to trash"),
  });

  const deleteFolderMutation = useMutation({
    mutationFn: filesApi.deleteFolder,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["files"] });
      queryClient.invalidateQueries({ queryKey: ["folders"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["storage", "usage"] });
      toast.success("Folder deleted");
    },
    onError: () => toast.error("Failed to delete folder"),
  });

  const createFolderMutation = useMutation({
    mutationFn: (name: string) => filesApi.createFolder(name, folderId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["files"] });
      queryClient.invalidateQueries({ queryKey: ["folders"] });
      setShowNewFolder(false);
      setNewFolderName("");
      toast.success("Folder created");
    },
    onError: () => toast.error("Failed to create folder"),
  });

  const renameFileMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => filesApi.renameFile(id, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["files"] });
      toast.success("File renamed");
    },
    onError: () => toast.error("Failed to rename file"),
  });

  const renameFolderMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => filesApi.renameFolder(id, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["files"] });
      queryClient.invalidateQueries({ queryKey: ["folders"] });
      toast.success("Folder renamed");
    },
    onError: () => toast.error("Failed to rename folder"),
  });

  const handleUploadFile = useCallback(
    async (
      file: File,
      options: { onProgress: (percent: number) => void; signal: AbortSignal },
    ) => {
      await filesApi.upload(file, folderId, {
        onUploadProgress: options.onProgress,
        signal: options.signal,
      });
    },
    [folderId],
  );

  const handleUploadComplete = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["files"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    queryClient.invalidateQueries({ queryKey: ["storage", "usage"] });
  }, [queryClient]);

  const breadcrumbs: BreadcrumbItem[] | null = folderId
    ? [{ label: "Files", href: "/files" }, { label: currentFolder?.name ?? "\u2026" }]
    : null;

  const folders = folderItems ?? [];
  const rawFiles = data?.data ?? [];

  const sortedFiles = [...rawFiles].sort((a, b) => {
    const dir = sortConfig.direction === "asc" ? 1 : -1;
    switch (sortConfig.field) {
      case "name":
        return dir * a.name.localeCompare(b.name);
      case "size":
        return dir * (a.size - b.size);
      case "updatedAt":
        return dir * (new Date(a.updatedAt).getTime() - new Date(b.updatedAt).getTime());
      case "createdAt":
        return dir * (new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
      default:
        return 0;
    }
  });

  const files = sortedFiles;
  const items = [...folders, ...files];

  const toggleSort = (field: SortField) => {
    if (sortConfig.field === field) {
      setSortConfig({ field, direction: sortConfig.direction === "asc" ? "desc" : "asc" });
    } else {
      setSortConfig({ field, direction: "asc" });
    }
  };

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    const allIds = items.map((i) => i.id);
    setSelectedIds(new Set(allIds));
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
    setSelectionActive(false);
  };

  const handleBulkDelete = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const folderIds = folders.filter((f) => selectedIds.has(f.id)).map((f) => f.id);
    const fileIds = ids.filter((id) => !folderIds.includes(id));

    const fileResult = await runDeletions(fileIds, (id) => filesApi.delete(id));
    const folderResult = await runDeletions(folderIds, (id) => filesApi.deleteFolder(id));

    queryClient.invalidateQueries({ queryKey: ["files"] });
    queryClient.invalidateQueries({ queryKey: ["folders"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    queryClient.invalidateQueries({ queryKey: ["storage", "usage"] });

    const msgs: string[] = [];
    if (fileResult.succeeded > 0) msgs.push(`${fileResult.succeeded} file${fileResult.succeeded > 1 ? "s" : ""} moved to trash`);
    if (folderResult.succeeded > 0) msgs.push(`${folderResult.succeeded} folder${folderResult.succeeded > 1 ? "s" : ""} deleted`);
    if (msgs.length > 0) toast.success(msgs.join(", "));
    const failed = fileResult.failed + folderResult.failed;
    if (failed > 0) toast.error(`${failed} item${failed > 1 ? "s" : ""} failed to delete`);
    clearSelection();
  };

  const handleDownload = useCallback(
    async (id: string, name: string) => {
      try {
        const url = await filesApi.getDownloadUrl(id);
        const a = document.createElement("a");
        a.href = url;
        a.download = name;
        a.rel = "noopener";
        document.body.appendChild(a);
        a.click();
        a.remove();
        toast.success(`Downloading ${name}`);
      } catch {
        toast.error("Download failed");
      }
    },
    []
  );

  const { getRootProps, isDragActive } = useDropzone({
    onDrop: (files) => {
      setShowUpload(true);
      // Defer so the dropzone component mounts before we push files into it
      setTimeout(() => uploadDropzoneRef.current?.addFiles(files), 0);
    },
    noClick: true,
    noKeyboard: true,
    multiple: true,
  });

  return (
    <div {...getRootProps()} className="max-w-7xl mx-auto space-y-6 relative">
      {isDragActive && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-otter-600/10 backdrop-blur-sm">
          <div className="bg-white rounded-2xl shadow-2xl border-2 border-dashed border-otter-500 p-12 text-center">
            <Upload size={48} className="mx-auto mb-4 text-otter-600" />
            <p className="text-lg font-semibold text-gray-900">Drop files to upload</p>
            <p className="text-sm text-gray-500 mt-1">Files will be added to the current folder</p>
          </div>
        </div>
      )}
      {breadcrumbs && <Breadcrumb items={breadcrumbs} />}

      {/* Bulk action bar */}
      {selectionActive && selectedIds.size > 0 && (
        <div className="flex items-center justify-between px-4 py-3 bg-otter-50 border border-otter-200 rounded-lg">
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-otter-800">
              {selectedIds.size} selected
            </span>
            <button
              onClick={selectAll}
              className="text-sm text-otter-600 hover:text-otter-800 underline"
            >
              Select all
            </button>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleBulkDelete}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-600 bg-white border border-red-200 rounded-lg hover:bg-red-50 transition"
            >
              <Trash2 size={14} />
              Delete
            </button>
            <button
              onClick={clearSelection}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition"
            >
              <X size={14} />
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold text-gray-900">Files</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              setSelectionActive(!selectionActive);
              if (selectionActive) clearSelection();
            }}
            className={cn(
              "flex items-center gap-2 px-3 py-2 text-sm border rounded-lg transition",
              selectionActive
                ? "text-otter-700 bg-otter-50 border-otter-300"
                : "text-gray-700 bg-white border-gray-300 hover:bg-gray-50"
            )}
          >
            <CheckSquare size={16} />
            Select
          </button>
          <button
            onClick={() => setShowNewFolder(true)}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition"
          >
            <FolderPlus size={16} />
            New folder
          </button>
          <button
            onClick={() => setShowUpload(!showUpload)}
            className="flex items-center gap-2 px-3 py-2 text-sm text-white bg-otter-600 rounded-lg hover:bg-otter-700 transition"
          >
            <Upload size={16} />
            Upload
          </button>

          <select
            value={`${sortConfig.field}-${sortConfig.direction}`}
            onChange={(e) => {
              const [field, direction] = e.target.value.split("-") as [SortField, "asc" | "desc"];
              setSortConfig({ field, direction });
            }}
            className="px-3 py-2 text-sm border border-gray-300 rounded-lg bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-otter-500"
          >
            <option value="updatedAt-desc">Last modified</option>
            <option value="updatedAt-asc">Oldest modified</option>
            <option value="name-asc">Name A–Z</option>
            <option value="name-desc">Name Z–A</option>
            <option value="size-desc">Largest first</option>
            <option value="size-asc">Smallest first</option>
            <option value="createdAt-desc">Newest created</option>
            <option value="createdAt-asc">Oldest created</option>
          </select>

          <div className="flex items-center border border-gray-300 rounded-lg overflow-hidden ml-2">
            <ViewModeButton
              mode="grid"
              current={viewMode}
              onClick={() => setViewMode("grid")}
              icon={LayoutGrid}
            />
            <ViewModeButton
              mode="list"
              current={viewMode}
              onClick={() => setViewMode("list")}
              icon={List}
            />
          </div>
        </div>
      </div>

      {/* New folder input */}
      {showNewFolder && (
        <div className="flex items-center gap-2 p-3 bg-white rounded-lg border border-gray-200">
          <FolderPlus size={20} className="text-amber-500" />
          <input
            type="text"
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            placeholder="Folder name"
            className="flex-1 px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-otter-500"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter" && newFolderName.trim()) {
                createFolderMutation.mutate(newFolderName.trim());
              }
              if (e.key === "Escape") setShowNewFolder(false);
            }}
          />
          <button
            onClick={() => {
              if (newFolderName.trim()) createFolderMutation.mutate(newFolderName.trim());
            }}
            className="px-3 py-1.5 bg-otter-600 text-white rounded-lg text-sm hover:bg-otter-700 transition"
          >
            Create
          </button>
          <button
            onClick={() => setShowNewFolder(false)}
            className="px-3 py-1.5 bg-gray-100 text-gray-700 rounded-lg text-sm hover:bg-gray-200 transition"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Upload dropzone */}
      {showUpload && (
        <FileUploadDropzone
          ref={uploadDropzoneRef}
          uploadFile={handleUploadFile}
          onUploadComplete={handleUploadComplete}
          onDismiss={() => setShowUpload(false)}
        />
      )}

      {/* File listing */}
      {isLoading && (viewMode === "list" ? <FileListSkeleton /> : <FileGridSkeleton />)}

      {!isLoading && items.length === 0 && (
        <EmptyState
          icon={FolderOpen}
          title="This folder is empty"
          description="Upload files or create folders to get started"
          action={{ label: "Upload files", onClick: () => setShowUpload(true) }}
        />
      )}

      {!isLoading && items.length > 0 && (
        <div className="space-y-6">
          {viewMode === "list" && (
            <div className="flex items-center gap-4 px-4 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-200">
              {selectionActive && <div className="w-4" />}
              <div className="w-10" />
              <SortableHeader field="name" label="Name" current={sortConfig} onSort={toggleSort} className="flex-1" />
              <SortableHeader field="updatedAt" label="Modified" current={sortConfig} onSort={toggleSort} className="w-32 hidden sm:block" />
              <SortableHeader field="size" label="Size" current={sortConfig} onSort={toggleSort} className="w-20 hidden sm:block" />
              <div className="w-8" />
            </div>
          )}
          {/* Folders */}
          {folders.length > 0 && (
            <section>
              {viewMode === "grid" && <h2 className="text-sm font-medium text-gray-500 mb-3">Folders</h2>}
              <div
                className={cn(
                  viewMode === "grid"
                    ? "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3"
                    : "space-y-1"
                )}
              >
                {folders.map((folder) => (
                  <FolderCard
                    key={folder.id}
                    folder={folder}
                    view={viewMode}
                    onDelete={(id) => deleteFolderMutation.mutate(id)}
                    onRename={(id, name) => renameFolderMutation.mutate({ id, name })}
                    selected={selectedIds.has(folder.id)}
                    onSelect={toggleSelect}
                    selectionActive={selectionActive}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Files */}
          {files.length > 0 && (
            <section>
              {viewMode === "grid" && <h2 className="text-sm font-medium text-gray-500 mb-3">Files</h2>}
              <div
                className={cn(
                  viewMode === "grid"
                    ? "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3"
                    : "space-y-1"
                )}
              >
                {files.map((file) => (
                  <FileCard
                    key={file.id}
                    file={file}
                    view={viewMode}
                    onDelete={(id) => deleteMutation.mutate(id)}
                    onShare={(id) => setShareFileId(id)}
                    onRename={(id, name) => renameFileMutation.mutate({ id, name })}
                    onDownload={handleDownload}
                    selected={selectedIds.has(file.id)}
                    onSelect={toggleSelect}
                    selectionActive={selectionActive}
                  />
                ))}
              </div>
            </section>
          )}
        </div>
      )}
      {shareFileId && (() => {
        const shareFile = files.find((f) => f.id === shareFileId);
        if (!shareFile) return null;
        return (
          <ShareDialog
            fileId={shareFile.id}
            fileName={shareFile.name}
            ownerId={shareFile.ownerId}
            sharedWith={shareFile.sharedWith}
            onShare={async (email, permission) => {
              await filesApi.share(shareFile.id, email, permission);
              queryClient.invalidateQueries({ queryKey: ["files"] });
            }}
            onPermissionChange={async (userId, permission) => {
              await filesApi.updateSharePermission(shareFile.id, userId, permission);
              queryClient.invalidateQueries({ queryKey: ["files"] });
            }}
            onRemoveAccess={async (userId) => {
              await filesApi.removeShare(shareFile.id, userId);
              queryClient.invalidateQueries({ queryKey: ["files"] });
            }}
            onClose={() => setShareFileId(null)}
          />
        );
      })()}
    </div>
  );
}

function ViewModeButton({
  mode,
  current,
  onClick,
  icon: Icon,
}: Readonly<{
  mode: ViewMode;
  current: ViewMode;
  onClick: () => void;
  icon: typeof LayoutGrid;
}>) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "p-2 transition",
        mode === current
          ? "bg-gray-100 text-gray-900"
          : "text-gray-400 hover:text-gray-600 hover:bg-gray-50"
      )}
    >
      <Icon size={16} />
    </button>
  );
}

function SortIndicator({
  isActive,
  direction,
}: Readonly<{ isActive: boolean; direction: "asc" | "desc" }>) {
  if (!isActive) {
    return <ArrowUpDown size={12} className="opacity-0 group-hover/sort:opacity-100" />;
  }
  return direction === "asc" ? <ChevronUp size={12} /> : <ChevronDown size={12} />;
}

function SortableHeader({
  field,
  label,
  current,
  onSort,
  className,
}: Readonly<{
  field: SortField;
  label: string;
  current: { field: SortField; direction: "asc" | "desc" };
  onSort: (field: SortField) => void;
  className?: string;
}>) {
  const isActive = current.field === field;

  return (
    <button
      onClick={() => onSort(field)}
      className={cn(
        "group/sort flex items-center gap-1 hover:text-gray-700 transition",
        isActive && "text-gray-900",
        className
      )}
    >
      {label}
      <SortIndicator isActive={isActive} direction={current.direction} />
    </button>
  );
}

export default function FilesPage() {
  return (
    <AppShell>
      <ErrorBoundary>
        <Suspense fallback={<PageLoader />}>
          <FileBrowserContent />
        </Suspense>
      </ErrorBoundary>
    </AppShell>
  );
}
