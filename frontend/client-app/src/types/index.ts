// ============================================================
// OtterWorks Shared Types
// ============================================================

export interface User {
  id: string;
  email: string;
  displayName: string;
  avatarUrl?: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface RegisterCredentials {
  displayName: string;
  email: string;
  password: string;
  confirmPassword: string;
}

export interface FileItem {
  id: string;
  name: string;
  mimeType: string;
  size: number;
  parentId: string | null;
  ownerId: string;
  ownerName: string;
  isFolder: boolean;
  isTrashed?: boolean;
  path: string;
  thumbnailUrl?: string;
  downloadUrl?: string;
  sharedWith: SharedUser[];
  tags: string[];
  createdAt: string;
  updatedAt: string;
  trashedAt?: string;
  versions: FileVersion[];
}

export interface FileVersion {
  id: string;
  fileId: string;
  versionNumber: number;
  size: number;
  uploadedBy: string;
  createdAt: string;
  downloadUrl: string;
}

export interface SharedUser {
  userId: string;
  name: string;
  email: string;
  avatarUrl?: string;
  permission: "view" | "edit" | "admin";
}

export interface Document {
  id: string;
  title: string;
  content: string;
  ownerId: string;
  ownerName: string;
  parentId: string | null;
  sharedWith: SharedUser[];
  collaborators: Collaborator[];
  tags: string[];
  wordCount: number;
  createdAt: string;
  updatedAt: string;
  trashedAt?: string;
  isArchived?: boolean;
  archivedAt?: string | null;
}

export interface Collaborator {
  userId: string;
  name: string;
  avatarUrl?: string;
  color: string;
  isOnline: boolean;
}

export interface Notification {
  id: string;
  type: "share" | "comment" | "mention" | "edit" | "system";
  title: string;
  message: string;
  read: boolean;
  actorId?: string;
  actorName?: string;
  actorAvatarUrl?: string;
  resourceId?: string;
  resourceType?: "file" | "document";
  createdAt: string;
}

export interface SearchResult {
  id: string;
  type: "file" | "document" | "folder";
  name: string;
  snippet?: string;
  path: string;
  updatedAt: string;
  ownerName: string;
}

export interface SearchFilters {
  query: string;
  type?: "file" | "document" | "folder" | "all";
  dateFrom?: string;
  dateTo?: string;
  owner?: string;
  tags?: string[];
}

export interface ActivityItem {
  id: string;
  type: "upload" | "edit" | "share" | "comment" | "delete" | "restore";
  description: string;
  actorName: string;
  actorAvatarUrl?: string;
  resourceName: string;
  resourceType: "file" | "document";
  resourceId: string;
  createdAt: string;
}

export interface StorageUsage {
  used: number;
  total: number;
  fileCount: number;
  documentCount: number;
}

export interface UserSettings {
  notificationEmail: boolean;
  notificationInApp: boolean;
  notificationDesktop: boolean;
  theme: "light" | "dark" | "system";
  language: string;
}

export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
}

export type ViewMode = "grid" | "list";

export type SortField = "name" | "updatedAt" | "size" | "createdAt";
export type SortDirection = "asc" | "desc";

export interface SortConfig {
  field: SortField;
  direction: SortDirection;
}
