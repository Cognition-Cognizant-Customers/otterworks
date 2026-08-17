import { Link } from "react-router-dom";
import { FileText, MoreVertical, Trash2, Share2, ExternalLink, Star } from "lucide-react";
import { useState, useEffect, useCallback } from "react";
import type { Document } from "@/types";
import { formatRelativeTime, getInitials, generateColor } from "@/lib/utils";
import { starredApi } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";

interface DocumentCardProps {
  document: Document;
  onDelete?: (id: string) => void;
  onShare?: (id: string) => void;
  view?: "grid" | "list";
  onStarToggle?: () => void;
}

export function DocumentCard({ document, onDelete, onShare, view = "grid", onStarToggle }: DocumentCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { user } = useAuthStore();
  const userId = user?.id ?? "";
  const [starred, setStarred] = useState(() => userId ? starredApi.isStarred(userId, document.id) : false);

  useEffect(() => {
    if (userId) {
      setStarred(starredApi.isStarred(userId, document.id));
    }
  }, [userId, document.id]);

  const handleStarClick = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!userId) return;
    const nowStarred = starredApi.toggle(userId, document.id, "document");
    setStarred(nowStarred);
    onStarToggle?.();
  }, [userId, document.id, onStarToggle]);

  if (view === "list") {
    return (
      <Link
        to={`/documents/${document.id}`}
        className="flex items-center gap-4 px-4 py-3 hover:bg-gray-50 rounded-lg transition group"
      >
        <div className="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center flex-shrink-0">
          <FileText size={20} className="text-blue-600" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900 truncate">{document.title}</p>
          <p className="text-xs text-gray-500">
            {document.ownerName} &middot; {formatRelativeTime(document.updatedAt)}
            {document.wordCount > 0 && ` \u00B7 ${document.wordCount} words`}
          </p>
        </div>
        {(document.collaborators ?? []).length > 0 && (
          <div className="flex -space-x-1 mr-2">
            {document.collaborators.slice(0, 3).map((c) => (
              <div
                key={c.userId}
                className="w-6 h-6 rounded-full border-2 border-white flex items-center justify-center text-[10px] font-bold text-white"
                style={{ backgroundColor: c.color || generateColor(c.userId) }}
                title={c.name}
              >
                {getInitials(c.name)}
              </div>
            ))}
          </div>
        )}
        <button
          onClick={handleStarClick}
          className="p-1 rounded hover:bg-gray-200 transition flex-shrink-0"
          aria-label={starred ? "Unstar" : "Star"}
        >
          <Star
            size={16}
            className={starred ? "text-yellow-400 fill-yellow-400" : "text-gray-400 opacity-0 group-hover:opacity-100"}
          />
        </button>
        <div className="relative">
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setMenuOpen(!menuOpen);
            }}
            className="p-1 rounded hover:bg-gray-200 text-gray-400 opacity-0 group-hover:opacity-100 transition"
          >
            <MoreVertical size={16} />
          </button>
          {menuOpen && (
            <DocMenu
              docId={document.id}
              onClose={() => setMenuOpen(false)}
              onDelete={onDelete}
              onShare={onShare}
            />
          )}
        </div>
      </Link>
    );
  }

  return (
    <Link
      to={`/documents/${document.id}`}
      className="group relative flex flex-col rounded-xl border border-gray-200 bg-white hover:shadow-md transition overflow-hidden"
    >
      {/* Preview area */}
      <div className="h-32 bg-gradient-to-br from-blue-50 to-otter-50 p-4 flex items-start">
        <p className="text-xs text-gray-500 line-clamp-4 leading-relaxed">
          {document.content
            ? document.content.replace(/<[^>]{0,2048}>/g, "").slice(0, 200)
            : "Empty document"}
        </p>
      </div>

      <div className="p-4">
        <div className="flex items-start justify-between mb-2">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-gray-900 truncate">{document.title}</p>
            <p className="text-xs text-gray-500 mt-0.5">
              {formatRelativeTime(document.updatedAt)}
            </p>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={handleStarClick}
              className="p-1 rounded hover:bg-gray-100 transition"
              aria-label={starred ? "Unstar" : "Star"}
            >
              <Star
                size={16}
                className={starred ? "text-yellow-400 fill-yellow-400" : "text-gray-400 opacity-0 group-hover:opacity-100"}
              />
            </button>
            <div className="relative">
              <button
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setMenuOpen(!menuOpen);
                }}
                className="p-1 rounded hover:bg-gray-100 text-gray-400 opacity-0 group-hover:opacity-100 transition"
              >
                <MoreVertical size={16} />
              </button>
              {menuOpen && (
                <DocMenu
                  docId={document.id}
                  onClose={() => setMenuOpen(false)}
                  onDelete={onDelete}
                  onShare={onShare}
                />
              )}
            </div>
          </div>
        </div>

        {(document.collaborators ?? []).length > 0 && (
          <div className="flex -space-x-1 mt-2">
            {document.collaborators.slice(0, 4).map((c) => (
              <div
                key={c.userId}
                className="w-6 h-6 rounded-full border-2 border-white flex items-center justify-center text-[10px] font-bold text-white"
                style={{ backgroundColor: c.color || generateColor(c.userId) }}
                title={c.name}
              >
                {getInitials(c.name)}
              </div>
            ))}
          </div>
        )}
      </div>
    </Link>
  );
}

function DocMenu({
  docId,
  onClose,
  onDelete,
  onShare,
}: {
  docId: string;
  onClose: () => void;
  onDelete?: (id: string) => void;
  onShare?: (id: string) => void;
}) {
  return (
    <>
      <button
        type="button"
        aria-label="Close menu"
        className="fixed inset-0 z-10 cursor-default"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onClose();
        }}
      />
      <div className="absolute right-0 top-full mt-1 w-40 bg-white rounded-lg shadow-lg border border-gray-200 py-1 z-20">
        <Link
          to={`/documents/${docId}`}
          onClick={(e) => e.stopPropagation()}
          className="flex items-center gap-2 w-full px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
        >
          <ExternalLink size={14} />
          Open
        </Link>
        <button
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onShare?.(docId);
            onClose();
          }}
          className="flex items-center gap-2 w-full px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
        >
          <Share2 size={14} />
          Share
        </button>
        <button
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onDelete?.(docId);
            onClose();
          }}
          className="flex items-center gap-2 w-full px-3 py-2 text-sm text-red-600 hover:bg-red-50"
        >
          <Trash2 size={14} />
          Delete
        </button>
      </div>
    </>
  );
}
