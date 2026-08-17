import { cn } from "@/lib/utils";

/** Stable identities for fixed-length placeholder lists. */
export function placeholderKeys(prefix: string, count: number): string[] {
  return Array.from({ length: count }, (_, i) => `${prefix}-${i}`);
}

function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-lg bg-gray-200",
        className
      )}
    />
  );
}

export function FileCardSkeleton() {
  return (
    <div className="flex flex-col rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex items-start justify-between mb-3">
        <Skeleton className="w-12 h-12 rounded-lg" />
        <Skeleton className="w-6 h-6 rounded" />
      </div>
      <Skeleton className="h-4 w-3/4 mb-2" />
      <Skeleton className="h-3 w-1/2" />
    </div>
  );
}

export function FileListRowSkeleton() {
  return (
    <div className="flex items-center gap-4 px-4 py-2.5">
      <Skeleton className="w-10 h-10 rounded-lg flex-shrink-0" />
      <Skeleton className="h-4 flex-1 max-w-[200px]" />
      <Skeleton className="h-3 w-24 hidden sm:block" />
      <Skeleton className="h-3 w-16 hidden sm:block" />
      <div className="w-8" />
    </div>
  );
}

export function FileGridSkeleton({ count = 8 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
      {placeholderKeys("file-card", count).map((key) => (
        <FileCardSkeleton key={key} />
      ))}
    </div>
  );
}

export function FileListSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="space-y-1">
      {placeholderKeys("file-row", count).map((key) => (
        <FileListRowSkeleton key={key} />
      ))}
    </div>
  );
}

export function DashboardSkeleton() {
  return (
    <div className="max-w-7xl mx-auto space-y-8">
      {/* Stats row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {placeholderKeys("stat", 4).map((key) => (
          <div key={key} className="bg-white rounded-xl border border-gray-200 p-5">
            <Skeleton className="h-3 w-20 mb-3" />
            <Skeleton className="h-8 w-16 mb-2" />
            <Skeleton className="h-2 w-24" />
          </div>
        ))}
      </div>
      {/* Content sections */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-white rounded-xl border border-gray-200 p-6">
          <Skeleton className="h-5 w-32 mb-4" />
          <div className="space-y-3">
            {placeholderKeys("activity-row", 4).map((key) => (
              <div key={key} className="flex items-center gap-3">
                <Skeleton className="w-10 h-10 rounded-lg" />
                <div className="flex-1">
                  <Skeleton className="h-4 w-40 mb-1" />
                  <Skeleton className="h-3 w-24" />
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <Skeleton className="h-5 w-24 mb-4" />
          <Skeleton className="h-32 w-full rounded-lg mb-4" />
          <Skeleton className="h-3 w-28" />
        </div>
      </div>
    </div>
  );
}

export { Skeleton };
