import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { QuotaUser, getUsers, updateStorageQuota } from "@/lib/api";
import { Spinner } from "@/components/Spinner";

const GB = 1024 * 1024 * 1024;
const QUOTA_OPTIONS = [1, 2, 5, 10, 20, 50].map((n) => n * GB);
const PAGE_SIZE_OPTIONS = [5, 10, 25];
const USERS_QUERY_KEY = ["quota-users"];

type SortColumn = "displayName" | "storageUsed" | "storageQuota";
type SortDirection = "asc" | "desc" | "";

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

function usagePercent(user: QuotaUser): number {
  return user.storageQuota > 0 ? (user.storageUsed / user.storageQuota) * 100 : 0;
}

export function QuotasPage() {
  const queryClient = useQueryClient();

  const [filter, setFilter] = useState("");
  const [sortColumn, setSortColumn] = useState<SortColumn | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>("");
  const [pageSize, setPageSize] = useState(5);
  const [pageIndex, setPageIndex] = useState(0);
  const [updatingIds, setUpdatingIds] = useState<Set<string>>(new Set());

  const usersQuery = useQuery({ queryKey: USERS_QUERY_KEY, queryFn: getUsers });
  const users = useMemo(() => usersQuery.data ?? [], [usersQuery.data]);
  const loading = usersQuery.isPending;
  // Only treat errors as fatal when there is no data to show; a failed
  // background refetch keeps the cached table on screen.
  const loadFailed = usersQuery.isError && users.length === 0;

  const quotaMutation = useMutation({
    mutationFn: ({ user, quota }: { user: QuotaUser; quota: number }) =>
      updateStorageQuota(user.id, quota),
    onSuccess: (result, { user }) => {
      queryClient.setQueryData<QuotaUser[]>(USERS_QUERY_KEY, (prev) =>
        (prev ?? []).map((u) =>
          u.id === user.id
            ? { ...u, storageQuota: result.quotaBytes, storageUsed: result.usedBytes }
            : u
        )
      );
      toast.success(`Quota updated for ${user.displayName}`);
    },
    onError: (_err, { user }) => toast.error(`Failed to update quota for ${user.displayName}`),
    onSettled: (_data, _err, { user }) => {
      setUpdatingIds((prev) => {
        const next = new Set(prev);
        next.delete(user.id);
        return next;
      });
    },
  });

  const onUpdateQuota = (user: QuotaUser, quota: number) => {
    setUpdatingIds((prev) => new Set(prev).add(user.id));
    quotaMutation.mutate({ user, quota });
  };

  const filtered = useMemo(() => {
    const term = filter.trim().toLowerCase();
    if (!term) return users;
    return users.filter(
      (u) =>
        u.displayName.toLowerCase().includes(term) || u.email.toLowerCase().includes(term)
    );
  }, [users, filter]);

  const sorted = useMemo(() => {
    if (!sortColumn || !sortDirection) return filtered;
    const dir = sortDirection === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = a[sortColumn];
      const bv = b[sortColumn];
      if (typeof av === "string" && typeof bv === "string") {
        return av.localeCompare(bv) * dir;
      }
      return ((av as number) - (bv as number)) * dir;
    });
  }, [filtered, sortColumn, sortDirection]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const clampedPage = Math.min(pageIndex, pageCount - 1);
  const paged = sorted.slice(clampedPage * pageSize, (clampedPage + 1) * pageSize);
  const rangeStart = sorted.length === 0 ? 0 : clampedPage * pageSize + 1;
  const rangeEnd = Math.min((clampedPage + 1) * pageSize, sorted.length);

  const onSort = (column: SortColumn) => {
    if (sortColumn !== column) {
      setSortColumn(column);
      setSortDirection("asc");
    } else if (sortDirection === "asc") {
      setSortDirection("desc");
    } else {
      setSortColumn(null);
      setSortDirection("");
    }
  };

  const sortIndicator = (column: SortColumn) => {
    if (sortColumn !== column || !sortDirection) return null;
    return (
      <span className="material-icons sort-icon" aria-hidden>
        {sortDirection === "asc" ? "arrow_upward" : "arrow_downward"}
      </span>
    );
  };

  return (
    <div className="page-container quotas-page">
      <h1 className="page-title">Storage Quotas</h1>

      <div className="toolbar">
        <div className="form-field search-field">
          <label htmlFor="quota-search">Search users</label>
          <input
            id="quota-search"
            type="text"
            placeholder="Search by name or email"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setPageIndex(0);
            }}
          />
        </div>
      </div>

      {loading && <Spinner />}

      {!loading && loadFailed && (
        <div className="empty-state error-state">
          <span className="material-icons" aria-hidden>
            error_outline
          </span>
          <p>Failed to load users</p>
          <button type="button" className="btn btn-stroked" onClick={() => usersQuery.refetch()}>
            Retry
          </button>
        </div>
      )}

      {!loading && !loadFailed && (
        <div className="table-container">
          <table className="quotas-table">
            <thead>
              <tr>
                <th aria-sort={sortColumn === "displayName" ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}>
                  <button type="button" className="sort-header" onClick={() => onSort("displayName")}>
                    User {sortIndicator("displayName")}
                  </button>
                </th>
                <th aria-sort={sortColumn === "storageUsed" ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}>
                  <button type="button" className="sort-header" onClick={() => onSort("storageUsed")}>
                    Used {sortIndicator("storageUsed")}
                  </button>
                </th>
                <th aria-sort={sortColumn === "storageQuota" ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}>
                  <button type="button" className="sort-header" onClick={() => onSort("storageQuota")}>
                    Quota {sortIndicator("storageQuota")}
                  </button>
                </th>
                <th>Usage</th>
                <th>Update Quota</th>
              </tr>
            </thead>
            <tbody>
              {paged.map((user) => {
                const percent = usagePercent(user);
                return (
                  <tr key={user.id}>
                    <td>
                      <div className="user-cell">
                        <span className="material-icons user-avatar" aria-hidden>
                          account_circle
                        </span>
                        <div>
                          <div className="user-name">{user.displayName}</div>
                          <div className="user-email">{user.email}</div>
                        </div>
                      </div>
                    </td>
                    <td>{formatBytes(user.storageUsed)}</td>
                    <td>{formatBytes(user.storageQuota)}</td>
                    <td>
                      <div className="usage-cell">
                        <div
                          className={`progress-bar${percent > 90 ? " warn" : ""}`}
                          role="progressbar"
                          aria-valuenow={Math.round(percent)}
                          aria-valuemin={0}
                          aria-valuemax={100}
                        >
                          <div
                            className="progress-bar-fill"
                            style={{ width: `${Math.min(percent, 100)}%` }}
                          />
                        </div>
                        <span className="usage-label">{Math.round(percent)}%</span>
                      </div>
                    </td>
                    <td>
                      <div className="quota-actions">
                        <label className="visually-hidden" htmlFor={`quota-select-${user.id}`}>
                          Update quota for {user.displayName}
                        </label>
                        <select
                          id={`quota-select-${user.id}`}
                          className="quota-select"
                          value={
                            QUOTA_OPTIONS.includes(user.storageQuota) ? user.storageQuota : ""
                          }
                          disabled={updatingIds.has(user.id)}
                          onChange={(e) => onUpdateQuota(user, Number(e.target.value))}
                        >
                          {!QUOTA_OPTIONS.includes(user.storageQuota) && (
                            <option value="" disabled>
                              {formatBytes(user.storageQuota)}
                            </option>
                          )}
                          {QUOTA_OPTIONS.map((bytes) => (
                            <option key={bytes} value={bytes}>
                              {bytes / GB} GB
                            </option>
                          ))}
                        </select>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {paged.length === 0 && (
                <tr>
                  <td colSpan={5} className="table-empty">
                    {filter ? "No users match your search" : "No users found"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          <div className="paginator">
            <label htmlFor="page-size">Items per page:</label>
            <select
              id="page-size"
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setPageIndex(0);
              }}
            >
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            <span className="paginator-range">
              {rangeStart} – {rangeEnd} of {sorted.length}
            </span>
            <button
              type="button"
              className="btn btn-icon"
              aria-label="First page"
              disabled={clampedPage === 0}
              onClick={() => setPageIndex(0)}
            >
              <span className="material-icons" aria-hidden>
                first_page
              </span>
            </button>
            <button
              type="button"
              className="btn btn-icon"
              aria-label="Previous page"
              disabled={clampedPage === 0}
              onClick={() => setPageIndex(clampedPage - 1)}
            >
              <span className="material-icons" aria-hidden>
                chevron_left
              </span>
            </button>
            <button
              type="button"
              className="btn btn-icon"
              aria-label="Next page"
              disabled={clampedPage >= pageCount - 1}
              onClick={() => setPageIndex(clampedPage + 1)}
            >
              <span className="material-icons" aria-hidden>
                chevron_right
              </span>
            </button>
            <button
              type="button"
              className="btn btn-icon"
              aria-label="Last page"
              disabled={clampedPage >= pageCount - 1}
              onClick={() => setPageIndex(pageCount - 1)}
            >
              <span className="material-icons" aria-hidden>
                last_page
              </span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
