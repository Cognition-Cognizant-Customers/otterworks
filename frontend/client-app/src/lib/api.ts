import { apiClient } from "./api-client";
import type {
  User,
  AuthTokens,
  LoginCredentials,
  RegisterCredentials,
  FileItem,
  Document,
  Notification,
  SearchResult,
  SearchFilters,
  ActivityItem,
  StorageUsage,
  UserSettings,
  PaginatedResponse,
  SharedUser,
} from "@/types";

// Shape after the axios camelCase interceptor transforms the file-service response
interface RawShareItem {
  id: string;
  fileId: string;
  sharedWith: string;
  permission: string;
  sharedBy: string;
  createdAt: string;
}

interface RawFileItem {
  id: string;
  name: string;
  mimeType: string;
  sizeBytes: number;
  s3Key: string;
  folderId: string | null;
  ownerId: string;
  version: number;
  isTrashed: boolean;
  createdAt: string;
  updatedAt: string;
  sharedWith?: RawShareItem[];
}

interface RawFileListResponse {
  files: RawFileItem[];
  total: number;
  page: number;
  pageSize: number;
}

// Normalize a single file from the file-service format to the frontend FileItem shape
function mapRawFile(raw: RawFileItem): FileItem {
  return {
    id: raw.id,
    name: raw.name,
    mimeType: raw.mimeType ?? "application/octet-stream",
    size: raw.sizeBytes ?? 0,
    parentId: raw.folderId ?? null,
    ownerId: raw.ownerId ?? "",
    ownerName: "",
    isFolder: false,
    isTrashed: raw.isTrashed ?? false,
    path: `/${raw.name}`,
    downloadUrl: undefined,
    sharedWith: (() => {
      const mapped = (raw.sharedWith ?? []).map((s) => ({
        userId: s.sharedWith,
        name: "",
        email: "",
        permission: s.permission === "viewer" ? "view" as const : s.permission === "editor" ? "edit" as const : "view" as const,
      }));
      const seen = new Set<string>();
      return mapped.filter((s) => {
        if (seen.has(s.userId)) return false;
        seen.add(s.userId);
        return true;
      });
    })(),
    tags: [],
    createdAt: raw.createdAt ?? "",
    updatedAt: raw.updatedAt ?? "",
    versions: [],
  };
}

// ── Auth ──────────────────────────────────────────────────────
export const authApi = {
  login: async (credentials: LoginCredentials): Promise<AuthTokens> => {
    const { data } = await apiClient.post<AuthTokens>("/auth/login", credentials);
    return data;
  },
  register: async (credentials: RegisterCredentials): Promise<AuthTokens> => {
    const { displayName, email, password } = credentials;
    const { data } = await apiClient.post<AuthTokens>("/auth/register", {
      displayName,
      email,
      password,
    });
    return data;
  },
  getProfile: async (): Promise<User> => {
    const { data } = await apiClient.get<User>("/auth/profile");
    return data;
  },
  updateProfile: async (updates: Partial<User>): Promise<User> => {
    const { data } = await apiClient.patch<User>("/auth/profile", updates);
    return data;
  },
  logout: async (): Promise<void> => {
    await apiClient.post("/auth/logout");
  },
  lookupUser: async (email: string): Promise<{ id: string; email: string; displayName: string }> => {
    const { data } = await apiClient.get<{ id: string; email: string; displayName: string }>(
      "/auth/users/lookup",
      { params: { email } }
    );
    return data;
  },
  lookupUserById: async (id: string): Promise<{ id: string; email: string; displayName: string }> => {
    const { data } = await apiClient.get<{ id: string; email: string; displayName: string }>(
      `/auth/users/by-id/${id}`
    );
    return data;
  },
};

// ── Helpers ───────────────────────────────────────────────────
// Extract the user ID from the JWT stored in localStorage.
function getOwnerIdFromJwt(): string | null {
  if (typeof window === "undefined") return null;
  const token = localStorage.getItem("otter_access_token");
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, '+').replace(/_/g, '/')));
    return payload.sub ?? null;
  } catch {
    return null;
  }
}

// Backend file objects use different field names — normalise to frontend FileItem shape.
function normalizeFileItem(raw: Record<string, unknown>): FileItem {
  return {
    ...raw,
    size: (raw.sizeBytes ?? raw.size ?? 0) as number,
    mimeType: (raw.mimeType ?? raw.contentType ?? "") as string,
    parentId: (raw.folderId ?? raw.parentId ?? null) as string | null,
    isFolder: (raw.isFolder ?? false) as boolean,
    ownerName: (raw.ownerName ?? "") as string,
    path: (raw.path ?? "") as string,
    sharedWith: (raw.sharedWith ?? []) as SharedUser[],
    tags: (raw.tags ?? []) as string[],
    versions: (raw.versions ?? []) as FileItem["versions"],
  } as FileItem;
}

// ── Files ─────────────────────────────────────────────────────
export const filesApi = {
  list: async (
    parentId?: string | null,
    page = 1,
    pageSize = 50
  ): Promise<PaginatedResponse<FileItem>> => {
    const params: Record<string, string | number> = { page, page_size: pageSize };
    if (parentId) params.folder_id = parentId;
    const { data } = await apiClient.get<RawFileListResponse>("/files", { params });
    return {
      data: (data.files ?? []).map(mapRawFile),
      total: data.total ?? 0,
      page: data.page ?? page,
      pageSize: data.pageSize ?? pageSize,
      hasMore: ((data.page ?? page) * (data.pageSize ?? pageSize)) < (data.total ?? 0),
    };
  },
  get: async (id: string): Promise<FileItem> => {
    const { data } = await apiClient.get<RawFileItem>(`/files/${id}`);
    return mapRawFile(data);
  },
  upload: async (
    file: File,
    parentId?: string | null,
    options?: { onUploadProgress?: (percent: number) => void; signal?: AbortSignal },
  ): Promise<FileItem> => {
    const formData = new FormData();
    formData.append("file", file);
    if (parentId) formData.append("folder_id", parentId);

    // The file-service requires owner_id — extract it from the JWT sub claim
    const ownerId = getOwnerIdFromJwt();
    if (ownerId) formData.append("owner_id", ownerId);

    const { data } = await apiClient.post<{ file: RawFileItem }>("/files/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      signal: options?.signal,
      onUploadProgress: options?.onUploadProgress
        ? (event) => {
            if (event.total) {
              options.onUploadProgress!(Math.round((event.loaded * 100) / event.total));
            }
          }
        : undefined,
    });
    // Backend wraps response in { file: {...} }
    const raw = data.file ?? (data as unknown as RawFileItem);
    return mapRawFile(raw);
  },
  listFolders: async (parentId?: string | null): Promise<FileItem[]> => {
    const params: Record<string, string> = {};
    if (parentId) params.parent_id = parentId;
    const { data } = await apiClient.get<{ folders: Array<Record<string, unknown>> }>("/folders", { params });
    return (data.folders ?? []).map((f) => ({
      id: (f.id ?? "") as string,
      name: (f.name ?? "") as string,
      mimeType: "",
      size: 0,
      parentId: (f.parentId ?? null) as string | null,
      ownerId: (f.ownerId ?? "") as string,
      ownerName: "",
      isFolder: true,
      path: `/${f.name}`,
      sharedWith: [],
      tags: [],
      createdAt: (f.createdAt ?? "") as string,
      updatedAt: (f.updatedAt ?? "") as string,
      versions: [],
    }));
  },
  createFolder: async (name: string, parentId?: string | null): Promise<FileItem> => {
    const ownerId = getOwnerIdFromJwt();
    const { data } = await apiClient.post<any>("/folders", {
      name,
      parent_id: parentId ?? null,
      owner_id: ownerId,
    });
    return normalizeFileItem(data);
  },
  getDownloadUrl: async (id: string): Promise<string> => {
    const { data } = await apiClient.get<{ url: string; expiresInSecs: number }>(`/files/${id}/download`);
    // Presigned URLs from S3/LocalStack use the internal Docker hostname.
    // Rewrite to localhost so the browser can reach the endpoint.
    return data.url.replace("://localstack:", "://localhost:");
  },
  delete: async (id: string): Promise<void> => {
    await apiClient.post(`/files/${id}/trash`);
  },
  getFolder: async (id: string): Promise<FileItem> => {
    const { data } = await apiClient.get<Record<string, unknown>>(`/folders/${id}`);
    return normalizeFileItem({ ...data, isFolder: true });
  },
  deleteFolder: async (id: string): Promise<void> => {
    await apiClient.delete(`/folders/${id}`);
  },
  share: async (id: string, email: string, permission: "view" | "edit"): Promise<void> => {
    const user = await authApi.lookupUser(email);
    const sharedBy = getOwnerIdFromJwt();
    if (!sharedBy) throw new Error("Unable to determine current user");
    await apiClient.post(`/files/${id}/share`, {
      shared_with: user.id,
      permission: permission === "view" ? "viewer" : "editor",
      shared_by: sharedBy,
    });
  },
  removeShare: async (fileId: string, userId: string): Promise<void> => {
    await apiClient.delete(`/files/${fileId}/share/${userId}`);
  },
  updateSharePermission: async (
    fileId: string,
    userId: string,
    permission: "view" | "edit"
  ): Promise<void> => {
    const sharedBy = getOwnerIdFromJwt();
    if (!sharedBy) throw new Error("Unable to determine current user");
    await apiClient.post(`/files/${fileId}/share`, {
      shared_with: userId,
      permission: permission === "view" ? "viewer" : "editor",
      shared_by: sharedBy,
    });
  },
  restore: async (id: string): Promise<void> => {
    await apiClient.post(`/files/${id}/restore`);
  },
  getShared: async (page = 1, pageSize = 50): Promise<PaginatedResponse<FileItem>> => {
    const { data } = await apiClient.get<RawFileListResponse>("/files/shared", {
      params: { page, page_size: pageSize },
    });
    const items = (data.files ?? []).map(mapRawFile);
    return {
      data: items,
      total: data.total ?? items.length,
      page: data.page ?? page,
      pageSize: data.pageSize ?? pageSize,
      hasMore: (data.page ?? page) * (data.pageSize ?? pageSize) < (data.total ?? items.length),
    };
  },
  getTrashed: async (page = 1, pageSize = 50): Promise<PaginatedResponse<FileItem>> => {
    const params: Record<string, string | number> = { page, page_size: pageSize };
    const ownerId = getOwnerIdFromJwt();
    if (ownerId) params.owner_id = ownerId;
    const { data } = await apiClient.get<RawFileListResponse>("/files/trash", {
      params,
    });
    const items = (data.files ?? []).map(mapRawFile);
    return {
      data: items,
      total: data.total ?? items.length,
      page: data.page ?? page,
      pageSize: data.pageSize ?? pageSize,
      hasMore: (data.page ?? page) * (data.pageSize ?? pageSize) < (data.total ?? items.length),
    };
  },
  permanentDelete: async (id: string): Promise<void> => {
    await apiClient.delete(`/files/${id}`);
  },
  renameFile: async (id: string, name: string): Promise<FileItem> => {
    const { data } = await apiClient.patch<RawFileItem>(`/files/${id}/rename`, { name });
    return mapRawFile(data);
  },
  renameFolder: async (id: string, name: string): Promise<FileItem> => {
    const { data } = await apiClient.put<Record<string, unknown>>(`/folders/${id}`, { name });
    return normalizeFileItem({ ...data, isFolder: true });
  },
  getRecent: async (limit = 10): Promise<FileItem[]> => {
    const params: Record<string, string | number> = { page: 1, page_size: limit };
    const { data } = await apiClient.get<RawFileListResponse>("/files", { params });
    return (data.files ?? []).map((f) => normalizeFileItem(f as unknown as Record<string, unknown>));
  },
};

// ── Documents ─────────────────────────────────────────────────
export const documentsApi = {
  list: async (page = 1, pageSize = 50): Promise<PaginatedResponse<Document>> => {
    const { data } = await apiClient.get<PaginatedResponse<Document>>("/documents", {
      params: { page, pageSize },
    });
    return data;
  },
  get: async (id: string): Promise<Document> => {
    const { data } = await apiClient.get<Document>(`/documents/${id}`);
    return data;
  },
  create: async (title: string, parentId?: string | null): Promise<Document> => {
    const { data } = await apiClient.post<Document>("/documents", { title, parentId });
    return data;
  },
  update: async (id: string, updates: Partial<Document>): Promise<Document> => {
    const { data } = await apiClient.patch<Document>(`/documents/${id}`, updates);
    return data;
  },
  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/documents/${id}`);
  },
  share: async (id: string, users: SharedUser[]): Promise<void> => {
    await apiClient.post(`/documents/${id}/share`, { users });
  },
  restore: async (id: string): Promise<void> => {
    await apiClient.post(`/documents/${id}/restore`);
  },
  getRecent: async (limit = 10): Promise<Document[]> => {
    const { data } = await apiClient.get<{ items?: Document[] }>("/documents", {
      params: { page: 1, size: limit },
    });
    return data.items ?? [];
  },
};

// ── Search ────────────────────────────────────────────────────
export const searchApi = {
  search: async (filters: SearchFilters): Promise<PaginatedResponse<SearchResult>> => {
    const { query, type, dateFrom, dateTo, owner } = filters;
    const params: Record<string, string | number | undefined> = {
      q: query,
      type: type === "all" ? undefined : type,
      date_from: dateFrom,
      date_to: dateTo,
      owner_id: owner,
    };
    const { data } = await apiClient.get<Record<string, unknown>>("/search", { params });
    const rawResults = (data.results ?? []) as Record<string, unknown>[];
    const total = (data.total as number) ?? rawResults.length;
    const pg = (data.page as number) ?? 1;
    const ps = (data.page_size as number) ?? (data.pageSize as number) ?? 20;
    return {
      data: rawResults.map((r): SearchResult => ({
        id: String(r.id ?? ""),
        type: (r.type as SearchResult["type"]) ?? "file",
        name: String(r.title ?? r.name ?? r.id ?? ""),
        snippet: String(r.content_snippet ?? r.contentSnippet ?? ""),
        path: "",
        updatedAt: String(r.updated_at ?? r.updatedAt ?? ""),
        ownerName: "",
      })),
      total,
      page: pg,
      pageSize: ps,
      hasMore: pg * ps < total,
    };
  },
  suggest: async (query: string): Promise<string[]> => {
    const { data } = await apiClient.get<{ suggestions: string[] }>("/search/suggest", {
      params: { q: query },
    });
    return data.suggestions ?? [];
  },
};

// ── Notifications ─────────────────────────────────────────────
export const notificationsApi = {
  list: async (page = 1, pageSize = 20): Promise<PaginatedResponse<Notification>> => {
    const { data } = await apiClient.get<PaginatedResponse<Notification>>("/notifications", {
      params: { page, pageSize },
    });
    return data;
  },
  markRead: async (id: string): Promise<void> => {
    await apiClient.patch(`/notifications/${id}/read`);
  },
  markAllRead: async (): Promise<void> => {
    await apiClient.post("/notifications/read-all");
  },
  getUnreadCount: async (): Promise<number> => {
    const { data } = await apiClient.get<{ count: number }>("/notifications/unread-count");
    return data.count;
  },
};

// ── Activity ──────────────────────────────────────────────────
// Shape after the axios camelCase interceptor transforms the file-service response
interface RawActivityItem {
  id: string;
  type: string;
  description: string;
  actorName: string;
  resourceName: string;
  resourceType: string;
  resourceId: string;
  createdAt: string;
}

export const activityApi = {
  getRecent: async (limit = 20): Promise<ActivityItem[]> => {
    const { data } = await apiClient.get<{ items: RawActivityItem[] }>("/files/activity", {
      params: { limit },
    });
    return (data.items ?? []).map((raw) => ({
      id: raw.id,
      type: raw.type as ActivityItem["type"],
      description: raw.description,
      actorName: raw.actorName,
      resourceName: raw.resourceName,
      resourceType: raw.resourceType as "file" | "document",
      resourceId: raw.resourceId,
      createdAt: raw.createdAt,
    }));
  },
};

// ── Storage ───────────────────────────────────────────────────
export const storageApi = {
  getUsage: async (): Promise<StorageUsage> => {
    // The /storage/usage endpoint is not routed. Compute stats from
    // existing file and document list endpoints instead.
    const [fileRes, docRes] = await Promise.all([
      apiClient.get<RawFileListResponse>("/files", { params: { page: 1, page_size: 1 } }),
      apiClient.get<{ total?: number }>("/documents", { params: { page: 1, size: 1 } }),
    ]);

    const fileCount = fileRes.data.total ?? 0;
    const documentCount = docRes.data.total ?? 0;

    // Fetch all files to sum storage. The file-service caps page_size at 100,
    // so paginate through all pages to get an accurate total.
    let used = 0;
    if (fileCount > 0) {
      const PAGE_LIMIT = 100;
      let page = 1;
      let fetched = 0;
      while (fetched < fileCount) {
        const batch = await apiClient.get<RawFileListResponse>("/files", {
          params: { page, page_size: PAGE_LIMIT },
        });
        const files = batch.data.files ?? [];
        if (files.length === 0) break;
        used += files.reduce(
          (sum, f) => {
            const raw = f as unknown as Record<string, number>;
            return sum + (raw.sizeBytes ?? raw.size_bytes ?? 0);
          },
          0,
        );
        fetched += files.length;
        page += 1;
      }
    }

    return {
      used,
      total: 10 * 1024 * 1024 * 1024, // 10 GB default quota
      fileCount,
      documentCount,
    };
  },
};

// ── Starred (localStorage-backed) ─────────────────────────────
const STARRED_STORAGE_KEY = "otter_starred_items";

type StarredItemType = "file" | "folder" | "document";

interface StarredEntry {
  itemId: string;
  itemType: StarredItemType;
  starredAt: string;
}

function getStarredMap(): Record<string, StarredEntry> {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(STARRED_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Record<string, StarredEntry>) : {};
  } catch {
    return {};
  }
}

function saveStarredMap(map: Record<string, StarredEntry>): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(STARRED_STORAGE_KEY, JSON.stringify(map));
}

function starredKey(userId: string, itemId: string): string {
  return `${userId}:${itemId}`;
}

export const starredApi = {
  isStarred: (userId: string, itemId: string): boolean => {
    return starredKey(userId, itemId) in getStarredMap();
  },

  toggle: (userId: string, itemId: string, itemType: StarredItemType): boolean => {
    const map = getStarredMap();
    const key = starredKey(userId, itemId);
    if (key in map) {
      delete map[key];
      saveStarredMap(map);
      return false;
    }
    map[key] = { itemId, itemType, starredAt: new Date().toISOString() };
    saveStarredMap(map);
    return true;
  },

  getStarredIds: (userId: string): { fileIds: string[]; folderIds: string[]; documentIds: string[] } => {
    const map = getStarredMap();
    const prefix = `${userId}:`;
    const fileIds: string[] = [];
    const folderIds: string[] = [];
    const documentIds: string[] = [];
    for (const [key, entry] of Object.entries(map)) {
      if (!key.startsWith(prefix)) continue;
      if (entry.itemType === "file") fileIds.push(entry.itemId);
      else if (entry.itemType === "folder") folderIds.push(entry.itemId);
      else documentIds.push(entry.itemId);
    }
    return { fileIds, folderIds, documentIds };
  },
};

// ── Settings ──────────────────────────────────────────────────
export const settingsApi = {
  get: async (): Promise<UserSettings> => {
    const { data } = await apiClient.get<UserSettings>("/settings");
    return data;
  },
  update: async (settings: Partial<UserSettings>): Promise<UserSettings> => {
    const { data } = await apiClient.patch<UserSettings>("/settings", settings);
    return data;
  },
};
