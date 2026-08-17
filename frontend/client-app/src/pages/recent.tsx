import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Clock } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { FileCard } from "@/components/files/file-card";
import { DocumentCard } from "@/components/documents/document-card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { filesApi, documentsApi } from "@/lib/api";
import type { FileItem, Document } from "@/types";

type RecentItem =
  | { kind: "file"; data: FileItem; updatedAt: string }
  | { kind: "document"; data: Document; updatedAt: string };

function groupByTime(items: RecentItem[]): Record<string, RecentItem[]> {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfYesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  const startOfWeek = new Date(now.getFullYear(), now.getMonth(), now.getDate() - startOfToday.getDay());

  const groups: Record<string, RecentItem[]> = {
    Today: [],
    Yesterday: [],
    "This week": [],
    Earlier: [],
  };

  for (const item of items) {
    const d = new Date(item.updatedAt);
    if (d >= startOfToday) {
      groups["Today"].push(item);
    } else if (d >= startOfYesterday) {
      groups["Yesterday"].push(item);
    } else if (d >= startOfWeek) {
      groups["This week"].push(item);
    } else {
      groups["Earlier"].push(item);
    }
  }

  return groups;
}

export default function RecentPage() {
  return (
    <AppShell>
      <ErrorBoundary>
        <RecentContent />
      </ErrorBoundary>
    </AppShell>
  );
}

function RecentContent() {
  const { data: recentFiles, isLoading: filesLoading } = useQuery({
    queryKey: ["files", "recent", "all"],
    queryFn: () => filesApi.getRecent(50),
  });

  const { data: recentDocs, isLoading: docsLoading } = useQuery({
    queryKey: ["documents", "recent", "all"],
    queryFn: () => documentsApi.getRecent(50),
  });

  const isLoading = filesLoading || docsLoading;

  const grouped = useMemo(() => {
    const items: RecentItem[] = [];

    if (recentFiles) {
      for (const file of recentFiles) {
        items.push({ kind: "file", data: file, updatedAt: file.updatedAt });
      }
    }
    if (recentDocs) {
      for (const doc of recentDocs) {
        items.push({ kind: "document", data: doc, updatedAt: doc.updatedAt });
      }
    }

    items.sort(
      (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
    );

    return groupByTime(items);
  }, [recentFiles, recentDocs]);

  const totalItems = Object.values(grouped).reduce((s, g) => s + g.length, 0);

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Recent</h1>
        <p className="text-sm text-gray-500 mt-1">
          Files and documents you&apos;ve recently accessed or modified.
        </p>
      </div>

      {/* Loading state */}
      {isLoading && <RecentSkeleton />}

      {/* Empty state */}
      {!isLoading && totalItems === 0 && (
        <EmptyState
          icon={Clock}
          title="No recent items"
          description="Files and documents you open or edit will appear here."
        />
      )}

      {/* Grouped items */}
      {!isLoading &&
        (["Today", "Yesterday", "This week", "Earlier"] as const).map(
          (label) => {
            const items = grouped[label];
            if (!items || items.length === 0) return null;
            return (
              <section key={label}>
                <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">
                  {label}
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {items.map((item) =>
                    item.kind === "file" ? (
                      <FileCard
                        key={`file-${item.data.id}`}
                        file={item.data}
                        view="grid"
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
                    ) : (
                      <DocumentCard
                        key={`doc-${item.data.id}`}
                        document={item.data}
                        view="grid"
                      />
                    )
                  )}
                </div>
              </section>
            );
          }
        )}
    </div>
  );
}

function RecentSkeleton() {
  return (
    <div className="space-y-6">
      {[1, 2].map((section) => (
        <div key={section}>
          <div className="h-4 w-20 bg-gray-200 rounded animate-pulse mb-3" />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 3 }, (_, i) => `card-${i}`).map((key) => (
              <div
                key={key}
                className="flex flex-col rounded-xl border border-gray-200 bg-white p-4"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="w-12 h-12 rounded-lg bg-gray-200 animate-pulse" />
                  <div className="w-6 h-6 rounded bg-gray-200 animate-pulse" />
                </div>
                <div className="h-4 w-3/4 bg-gray-200 rounded animate-pulse mb-2" />
                <div className="h-3 w-1/2 bg-gray-200 rounded animate-pulse" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
