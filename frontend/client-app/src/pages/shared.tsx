import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Share2, LayoutGrid, List } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { FileCard } from "@/components/files/file-card";
import { PageLoader } from "@/components/ui/loading-spinner";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { filesApi } from "@/lib/api";
import { useUIStore } from "@/stores/ui-store";
import { cn } from "@/lib/utils";
import type { ViewMode } from "@/types";

export default function SharedPage() {
  return (
    <AppShell>
      <ErrorBoundary>
        <SharedContent />
      </ErrorBoundary>
    </AppShell>
  );
}

function SharedContent() {
  const { viewMode, setViewMode } = useUIStore();

  const { data, isLoading } = useQuery({
    queryKey: ["files", "shared"],
    queryFn: () => filesApi.getShared(),
  });

  const rawFiles = data?.data || [];
  // Deduplicate by file id (multiple share records for same file)
  const seen = new Set<string>();
  const files = rawFiles.filter((f) => {
    if (seen.has(f.id)) return false;
    seen.add(f.id);
    return true;
  });

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Shared with me</h1>
        <div className="flex items-center border border-gray-300 rounded-lg overflow-hidden">
          <ViewBtn
            mode="grid"
            current={viewMode}
            onClick={() => setViewMode("grid")}
            icon={LayoutGrid}
          />
          <ViewBtn
            mode="list"
            current={viewMode}
            onClick={() => setViewMode("list")}
            icon={List}
          />
        </div>
      </div>

      {isLoading && <PageLoader />}

      {!isLoading && files.length === 0 && (
        <EmptyState
          icon={Share2}
          title="Nothing shared with you yet"
          description="Files and documents shared with you will appear here"
        />
      )}

      {!isLoading && files.length > 0 && (
        <div
          className={cn(
            viewMode === "grid"
              ? "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4"
              : "space-y-1"
          )}
        >
          {files.map((file) => (
            <FileCard
              key={file.id}
              file={file}
              view={viewMode}
              onDownload={async (id, name) => {
                try {
                  const downloadUrl = await filesApi.getDownloadUrl(id);
                  const a = document.createElement("a");
                  a.href = downloadUrl;
                  a.download = name;
                  a.rel = "noopener";
                  document.body.appendChild(a);
                  a.click();
                  a.remove();
                  toast.success("File downloaded successfully");
                } catch {
                  toast.error("Download failed. Please try again.");
                }
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ViewBtn({
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
