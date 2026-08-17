import { useState, useCallback, useEffect, Suspense } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Search,
  FileText,
  FolderOpen,
  File,
  Filter,
  X,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { PageLoader } from "@/components/ui/loading-spinner";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { searchApi } from "@/lib/api";
import { formatRelativeTime, cn } from "@/lib/utils";

/**
 * Strip all HTML tags from a string except {@code <em>} (used by
 * MeiliSearch for search-hit highlighting).  Everything else is
 * escaped to prevent XSS.
 */
function sanitizeSnippet(html: string): string {
  const ALLOWED = /<\/?em>/gi;
  const tokens: string[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = ALLOWED.exec(html)) !== null) {
    if (match.index > lastIndex) {
      tokens.push(escapeHtml(html.slice(lastIndex, match.index)));
    }
    tokens.push(match[0].toLowerCase());
    lastIndex = ALLOWED.lastIndex;
  }
  if (lastIndex < html.length) {
    tokens.push(escapeHtml(html.slice(lastIndex)));
  }
  return tokens.join("");
}

const HTML_ENTITIES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
};

function escapeHtml(text: string): string {
  return text.replaceAll(/[&<>"]/g, (char) => HTML_ENTITIES[char]);
}
import type { SearchResult } from "@/types";

function SearchContent() {
  const [searchParams] = useSearchParams();
  const initialQuery = searchParams.get("q") || "";
  const [query, setQuery] = useState(initialQuery);
  const [submittedQuery, setSubmittedQuery] = useState(initialQuery);
  const [typeFilter, setTypeFilter] = useState<string>("all");

  useEffect(() => {
    const urlQuery = searchParams.get("q") || "";
    setQuery(urlQuery);
    setSubmittedQuery(urlQuery);
  }, [searchParams]);
  const [showFilters, setShowFilters] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["search", submittedQuery, typeFilter],
    queryFn: () =>
      searchApi.search({
        query: submittedQuery,
        type: typeFilter === "all" ? undefined : (typeFilter as "file" | "document" | "folder"),
      }),
    enabled: submittedQuery.length > 0,
  });

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      setSubmittedQuery(query.trim());
    },
    [query]
  );

  const results = data?.data || [];

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Search</h1>

      {/* Search form */}
      <form onSubmit={handleSubmit} className="relative">
        <Search
          size={20}
          className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400"
        />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search files, documents, and folders..."
          className="w-full pl-12 pr-20 py-3 bg-white border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-otter-500 focus:border-transparent"
          autoFocus
        />
        <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
          <button
            type="button"
            onClick={() => setShowFilters(!showFilters)}
            className={cn(
              "p-2 rounded-lg transition",
              showFilters ? "bg-otter-100 text-otter-700" : "text-gray-400 hover:text-gray-600"
            )}
          >
            <Filter size={16} />
          </button>
          {query && (
            <button
              type="button"
              onClick={() => {
                setQuery("");
                setSubmittedQuery("");
              }}
              className="p-2 text-gray-400 hover:text-gray-600"
            >
              <X size={16} />
            </button>
          )}
        </div>
      </form>

      {/* Filters */}
      {showFilters && (
        <div className="flex items-center gap-2">
          {["all", "file", "document", "folder"].map((type) => (
            <button
              key={type}
              onClick={() => setTypeFilter(type)}
              className={cn(
                "px-3 py-1.5 rounded-full text-xs font-medium transition",
                typeFilter === type
                  ? "bg-otter-600 text-white"
                  : "bg-gray-100 text-gray-600 hover:bg-gray-200"
              )}
            >
              {type === "all" ? "All types" : type.charAt(0).toUpperCase() + type.slice(1) + "s"}
            </button>
          ))}
        </div>
      )}

      {/* Results */}
      {submittedQuery && isLoading ? (
        <PageLoader />
      ) : submittedQuery && results.length === 0 ? (
        <EmptyState
          icon={Search}
          title="No results found"
          description={`No results for "${submittedQuery}". Try different keywords.`}
        />
      ) : submittedQuery ? (
        <div className="space-y-1">
          <p className="text-sm text-gray-500 mb-4">
            {data?.total || results.length} result{results.length !== 1 ? "s" : ""} for &ldquo;{submittedQuery}&rdquo;
          </p>
          {results.map((result) => (
            <SearchResultRow key={result.id} result={result} />
          ))}
        </div>
      ) : (
        <EmptyState
          icon={Search}
          title="Search OtterWorks"
          description="Find files, documents, and folders across your workspace"
        />
      )}
    </div>
  );
}

function SearchResultRow({ result }: Readonly<{ result: SearchResult }>) {
  const href =
    result.type === "document"
      ? `/documents/${result.id}`
      : result.type === "folder"
      ? `/files?folder=${result.id}`
      : `/files/${result.id}`;

  const Icon =
    result.type === "document"
      ? FileText
      : result.type === "folder"
      ? FolderOpen
      : File;

  const iconColor =
    result.type === "document"
      ? "text-blue-600 bg-blue-50"
      : result.type === "folder"
      ? "text-amber-600 bg-amber-50"
      : "text-otter-600 bg-otter-50";

  return (
    <Link
      to={href}
      className="flex items-start gap-4 px-4 py-3 rounded-lg hover:bg-gray-50 transition"
    >
      <div
        className={cn(
          "w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0",
          iconColor
        )}
      >
        <Icon size={20} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900">{result.name}</p>
        {result.snippet && (
          <p
            className="text-xs text-gray-500 mt-0.5 line-clamp-2 [&>em]:font-semibold [&>em]:not-italic [&>em]:text-gray-700"
            dangerouslySetInnerHTML={{
              __html: sanitizeSnippet(result.snippet),
            }}
          />
        )}
        <div className="flex items-center gap-2 mt-1">
          <span className="text-xs text-gray-400">{result.path}</span>
          <span className="text-xs text-gray-300">&middot;</span>
          <span className="text-xs text-gray-400">
            {formatRelativeTime(result.updatedAt)}
          </span>
          <span className="text-xs text-gray-300">&middot;</span>
          <span className="text-xs text-gray-400">{result.ownerName}</span>
        </div>
      </div>
    </Link>
  );
}

export default function SearchPage() {
  return (
    <AppShell>
      <ErrorBoundary>
        <Suspense fallback={<PageLoader />}>
          <SearchContent />
        </Suspense>
      </ErrorBoundary>
    </AppShell>
  );
}
