import { useState } from "react";
import { X, Link2, Copy, Check, UserPlus, Globe, Lock, ChevronDown } from "lucide-react";
import toast from "react-hot-toast";
import { cn } from "@/lib/utils";
import { filesApi } from "@/lib/api";
import type { SharedUser } from "@/types";

interface ShareDialogProps {
  fileId: string;
  fileName: string;
  ownerId?: string;
  ownerName?: string;
  ownerEmail?: string;
  sharedWith: SharedUser[];
  resolvedUsers?: Record<string, { name: string; email: string }>;
  onShare: (email: string, permission: "view" | "edit") => Promise<void>;
  onClose: () => void;
  onPermissionChange?: (userId: string, permission: "view" | "edit") => Promise<void>;
  onRemoveAccess?: (userId: string) => Promise<void>;
}

type LinkAccess = "restricted" | "anyone";

export function ShareDialog({
  fileId,
  fileName,
  ownerId,
  ownerName,
  ownerEmail,
  sharedWith,
  resolvedUsers = {},
  onShare,
  onClose,
  onPermissionChange,
  onRemoveAccess,
}: ShareDialogProps) {
  const [email, setEmail] = useState("");
  const [permission, setPermission] = useState<"view" | "edit">("view");
  const [isSharing, setIsSharing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState<"people" | "link">("people");
  const [linkAccess, setLinkAccess] = useState<LinkAccess>("restricted");
  const [updatingUserId, setUpdatingUserId] = useState<string | null>(null);
  const [removingUserId, setRemovingUserId] = useState<string | null>(null);

  const handleShare = async () => {
    if (!email.trim()) return;
    setIsSharing(true);
    try {
      await onShare(email.trim(), permission);
      setEmail("");
      toast.success(`Shared with ${email.trim()}`);
    } catch {
      toast.error("Failed to share file");
    } finally {
      setIsSharing(false);
    }
  };

  const handlePermissionChange = async (userId: string, newPermission: "view" | "edit") => {
    setUpdatingUserId(userId);
    try {
      if (onPermissionChange) {
        await onPermissionChange(userId, newPermission);
      } else {
        await filesApi.updateSharePermission(fileId, userId, newPermission);
      }
      toast.success("Permission updated");
    } catch {
      toast.error("Failed to update permission");
    } finally {
      setUpdatingUserId(null);
    }
  };

  const handleRemoveAccess = async (userId: string) => {
    setRemovingUserId(userId);
    try {
      if (onRemoveAccess) {
        await onRemoveAccess(userId);
      } else {
        await filesApi.removeShare(fileId, userId);
      }
      toast.success("Access removed");
    } catch {
      toast.error("Failed to remove access");
    } finally {
      setRemovingUserId(null);
    }
  };

  const handleCopyLink = async () => {
    const shareUrl = `${window.location.origin}/files/${fileId}`;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      toast.success("Link copied to clipboard");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Failed to copy link");
    }
  };

  const ownerInitial = ownerName ? ownerName.charAt(0).toUpperCase() : "O";

  return (
    <>
      <button
        type="button"
        aria-label="Close share dialog"
        className="fixed inset-0 bg-black/40 z-40 cursor-default"
        onClick={onClose}
      />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="share-dialog-title"
          className="bg-white rounded-2xl shadow-2xl w-full max-w-lg pointer-events-auto"
        >
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
            <h2 id="share-dialog-title" className="text-lg font-semibold text-gray-900">
              Share &ldquo;{fileName}&rdquo;
            </h2>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition"
            >
              <X size={18} />
            </button>
          </div>

          {/* Tabs */}
          <div className="flex border-b border-gray-200">
            <button
              onClick={() => setActiveTab("people")}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 px-4 py-3 text-sm font-medium transition",
                activeTab === "people"
                  ? "text-otter-600 border-b-2 border-otter-600"
                  : "text-gray-500 hover:text-gray-700"
              )}
            >
              <UserPlus size={16} />
              People
            </button>
            <button
              onClick={() => setActiveTab("link")}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 px-4 py-3 text-sm font-medium transition",
                activeTab === "link"
                  ? "text-otter-600 border-b-2 border-otter-600"
                  : "text-gray-500 hover:text-gray-700"
              )}
            >
              <Globe size={16} />
              Get link
            </button>
          </div>

          {/* Content */}
          <div className="px-6 py-5">
            {activeTab === "people" ? (
              <div className="space-y-4">
                {/* Email input + permission */}
                <div className="flex gap-2">
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="Add people by email"
                    className="flex-1 px-3.5 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-otter-500 focus:border-transparent transition"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleShare();
                    }}
                  />
                  <select
                    value={permission}
                    onChange={(e) => setPermission(e.target.value as "view" | "edit")}
                    className="px-3 py-2.5 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-otter-500"
                  >
                    <option value="view">Viewer</option>
                    <option value="edit">Editor</option>
                  </select>
                  <button
                    onClick={handleShare}
                    disabled={!email.trim() || isSharing}
                    className="px-4 py-2.5 bg-otter-600 text-white rounded-lg text-sm font-medium hover:bg-otter-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {isSharing ? "Sharing..." : "Share"}
                  </button>
                </div>

                {/* People with access list */}
                <div className="space-y-1">
                  <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                    People with access
                  </p>

                  {/* Owner row */}
                  {ownerId && (
                    <div className="flex items-center justify-between py-2">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-otter-600 flex items-center justify-center text-xs font-medium text-white">
                          {ownerInitial}
                        </div>
                        <div>
                          <p className="text-sm font-medium text-gray-900">
                            {ownerName || "Owner"}
                          </p>
                          {ownerEmail && (
                            <p className="text-xs text-gray-500">{ownerEmail}</p>
                          )}
                        </div>
                      </div>
                      <span className="text-xs text-gray-500 px-2 py-1 bg-gray-100 rounded-full">
                        Owner
                      </span>
                    </div>
                  )}

                  {/* Shared users */}
                  {sharedWith.length > 0 ? (
                    sharedWith
                      .filter((user) => user.userId !== ownerId)
                      .map((user) => {
                        const resolved = resolvedUsers[user.userId];
                        const displayName = resolved?.name || user.name || user.userId.slice(0, 8);
                        const displayEmail = resolved?.email || user.email;
                        const isUpdating = updatingUserId === user.userId;
                        const isRemoving = removingUserId === user.userId;
                        return (
                          <div
                            key={user.userId}
                            className="flex items-center justify-between py-2 group"
                          >
                            <div className="flex items-center gap-3 min-w-0">
                              <div className="w-8 h-8 rounded-full bg-otter-100 flex items-center justify-center text-xs font-medium text-otter-700 flex-shrink-0">
                                {displayName.charAt(0).toUpperCase()}
                              </div>
                              <div className="min-w-0">
                                <p className="text-sm font-medium text-gray-900 truncate">
                                  {displayName}
                                </p>
                                {displayEmail && (
                                  <p className="text-xs text-gray-500 truncate">{displayEmail}</p>
                                )}
                              </div>
                            </div>
                            <div className="flex items-center gap-1.5 flex-shrink-0">
                              <div className="relative">
                                <select
                                  value={user.permission === "edit" ? "edit" : "view"}
                                  onChange={(e) =>
                                    handlePermissionChange(
                                      user.userId,
                                      e.target.value as "view" | "edit"
                                    )
                                  }
                                  disabled={isUpdating || isRemoving}
                                  className={cn(
                                    "appearance-none pl-2 pr-6 py-1 text-xs rounded-full border cursor-pointer focus:outline-none focus:ring-2 focus:ring-otter-500 bg-white",
                                    isUpdating
                                      ? "opacity-50 cursor-wait"
                                      : "border-gray-200 text-gray-600 hover:border-gray-300"
                                  )}
                                >
                                  <option value="view">Viewer</option>
                                  <option value="edit">Editor</option>
                                </select>
                                <ChevronDown
                                  size={12}
                                  className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none"
                                />
                              </div>
                              <button
                                onClick={() => handleRemoveAccess(user.userId)}
                                disabled={isRemoving || isUpdating}
                                className={cn(
                                  "p-1 rounded-md transition",
                                  isRemoving
                                    ? "opacity-50 cursor-wait"
                                    : "text-gray-400 hover:text-red-500 hover:bg-red-50"
                                )}
                                title="Remove access"
                              >
                                <X size={14} />
                              </button>
                            </div>
                          </div>
                        );
                      })
                  ) : !ownerId ? (
                    <p className="text-sm text-gray-500 text-center py-4">
                      No one else has access yet
                    </p>
                  ) : null}

                  {ownerId && sharedWith.filter((u) => u.userId !== ownerId).length === 0 && (
                    <p className="text-sm text-gray-500 text-center py-3">
                      No one else has access yet
                    </p>
                  )}
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                {/* Link access mode toggle */}
                <div className="space-y-3">
                  <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                    General access
                  </p>
                  <button
                    onClick={() => {
                      setLinkAccess("restricted");
                    }}
                    className={cn(
                      "w-full flex items-center gap-3 p-3 rounded-xl border transition text-left",
                      linkAccess === "restricted"
                        ? "border-otter-300 bg-otter-50"
                        : "border-gray-200 hover:border-gray-300"
                    )}
                  >
                    <Lock
                      size={18}
                      className={cn(
                        linkAccess === "restricted" ? "text-otter-600" : "text-gray-400"
                      )}
                    />
                    <div className="flex-1">
                      <p
                        className={cn(
                          "text-sm font-medium",
                          linkAccess === "restricted" ? "text-otter-700" : "text-gray-700"
                        )}
                      >
                        Restricted
                      </p>
                      <p className="text-xs text-gray-500">
                        Only people explicitly shared with can access
                      </p>
                    </div>
                    {linkAccess === "restricted" && (
                      <Check size={16} className="text-otter-600 flex-shrink-0" />
                    )}
                  </button>
                  <button
                    onClick={() => {
                      setLinkAccess("anyone");
                    }}
                    className={cn(
                      "w-full flex items-center gap-3 p-3 rounded-xl border transition text-left",
                      linkAccess === "anyone"
                        ? "border-otter-300 bg-otter-50"
                        : "border-gray-200 hover:border-gray-300"
                    )}
                  >
                    <Globe
                      size={18}
                      className={cn(
                        linkAccess === "anyone" ? "text-otter-600" : "text-gray-400"
                      )}
                    />
                    <div className="flex-1">
                      <p
                        className={cn(
                          "text-sm font-medium",
                          linkAccess === "anyone" ? "text-otter-700" : "text-gray-700"
                        )}
                      >
                        Anyone with the link
                      </p>
                      <p className="text-xs text-gray-500">
                        Anyone with the link can view this file
                      </p>
                    </div>
                    {linkAccess === "anyone" && (
                      <Check size={16} className="text-otter-600 flex-shrink-0" />
                    )}
                  </button>
                </div>

                {/* Copy link section */}
                <div className="flex items-center gap-3 p-4 bg-gray-50 rounded-xl">
                  <Link2 size={20} className="text-gray-400 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-gray-700 truncate">
                      {typeof window !== "undefined"
                        ? `${window.location.origin}/files/${fileId}`
                        : `/files/${fileId}`}
                    </p>
                    <p className="text-xs text-gray-500 mt-0.5">
                      {linkAccess === "anyone"
                        ? "Anyone with the link can view this file"
                        : "Only people with access can open this link"}
                    </p>
                  </div>
                  <button
                    onClick={handleCopyLink}
                    className="flex items-center gap-1.5 px-3 py-2 bg-white border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition flex-shrink-0"
                  >
                    {copied ? (
                      <>
                        <Check size={14} className="text-green-500" />
                        Copied
                      </>
                    ) : (
                      <>
                        <Copy size={14} />
                        Copy link
                      </>
                    )}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
