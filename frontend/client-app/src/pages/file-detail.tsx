import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  ArrowLeft,
  Download,
  Loader2,
  Share2,
  Trash2,
  Clock,
  User,
  HardDrive,
  FileText,
  Image as ImageIcon,
  Film,
  File,
  Eye,
} from "lucide-react";
import { useState, useEffect } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { PageLoader } from "@/components/ui/loading-spinner";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { ShareDialog } from "@/components/files/share-dialog";
import { FilePreview } from "@/components/files/file-preview";
import { filesApi, authApi } from "@/lib/api";
import { formatFileSize, formatRelativeTime, getInitials, generateColor } from "@/lib/utils";

export default function FileDetailPage() {
  return (
    <AppShell>
      <ErrorBoundary>
        <FileDetailContent />
      </ErrorBoundary>
    </AppShell>
  );
}

function FileDetailContent() {
  const params = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const fileId = params.id as string;

  const { data: file, isLoading } = useQuery({
    queryKey: ["files", fileId],
    queryFn: () => filesApi.get(fileId),
  });

  const { data: presignedUrl, isLoading: isUrlLoading } = useQuery({
    queryKey: ["files", fileId, "download-url"],
    queryFn: () => filesApi.getDownloadUrl(fileId),
    enabled: !!file,
    staleTime: 30 * 60 * 1000,
  });

  const [showShareDialog, setShowShareDialog] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [resolvedUsers, setResolvedUsers] = useState<Record<string, { name: string; email: string }>>({});

  useEffect(() => {
    if (!file?.sharedWith?.length) return;
    const userIds = file.sharedWith
      .map((s) => s.userId)
      .filter((id) => id && !resolvedUsers[id]);
    if (userIds.length === 0) return;

    Promise.allSettled(
      userIds.map((id) => authApi.lookupUserById(id))
    ).then((results) => {
      const newUsers: Record<string, { name: string; email: string }> = {};
      results.forEach((result, i) => {
        if (result.status === "fulfilled") {
          newUsers[userIds[i]] = {
            name: result.value.displayName || result.value.email,
            email: result.value.email,
          };
        }
      });
      if (Object.keys(newUsers).length > 0) {
        setResolvedUsers((prev) => ({ ...prev, ...newUsers }));
      }
    });
  }, [file?.sharedWith]);

  const deleteMutation = useMutation({
    mutationFn: () => filesApi.delete(fileId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["files"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("File moved to trash");
      navigate("/files");
    },
    onError: () => toast.error("Failed to move file to trash"),
  });

  const handleShare = async (email: string, permission: "view" | "edit") => {
    await filesApi.share(fileId, email, permission);
    queryClient.invalidateQueries({ queryKey: ["files", fileId] });
  };

  if (isLoading) return <PageLoader />;
  if (!file) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px]">
        <p className="text-gray-500">File not found</p>
        <Link to="/files" className="text-otter-600 hover:underline mt-2 text-sm">
          Back to files
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <Breadcrumb
        items={[
          { label: "Files", href: "/files" },
          { label: file.name },
        ]}
      />

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate(-1)}
            className="p-2 rounded-lg hover:bg-gray-100 text-gray-500"
          >
            <ArrowLeft size={20} />
          </button>
          <div className="w-12 h-12 rounded-xl bg-otter-50 flex items-center justify-center">
            <FileIcon mimeType={file.mimeType} />
          </div>
          <div>
            <h1 className="text-xl font-bold text-gray-900">{file.name}</h1>
            <p className="text-sm text-gray-500">
              {formatFileSize(file.size)} &middot; {formatRelativeTime(file.updatedAt)}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            disabled={isDownloading}
            onClick={async () => {
              setIsDownloading(true);
              try {
                const downloadUrl = await filesApi.getDownloadUrl(file.id);
                const a = document.createElement("a");
                a.href = downloadUrl;
                a.download = file.name;
                a.rel = "noopener";
                document.body.appendChild(a);
                a.click();
                a.remove();
                toast.success(`Downloading ${file.name}`);
              } catch {
                toast.error("Download failed. Please try again.");
              } finally {
                setIsDownloading(false);
              }
            }}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isDownloading ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
            {isDownloading ? "Downloading..." : "Download"}
          </button>
          <button
            onClick={() => setShowShareDialog(true)}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition"
          >
            <Share2 size={16} />
            Share
          </button>
          <button
            onClick={() => deleteMutation.mutate()}
            className="flex items-center gap-2 px-3 py-2 text-sm text-red-600 bg-white border border-red-200 rounded-lg hover:bg-red-50 transition"
          >
            <Trash2 size={16} />
            Delete
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Preview */}
        <div className="lg:col-span-2">
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="px-5 py-4 border-b border-gray-200">
              <h2 className="text-sm font-medium text-gray-700 flex items-center gap-2">
                <Eye size={16} />
                Preview
              </h2>
            </div>
            <div className="p-8 flex items-center justify-center min-h-[300px] bg-gray-50">
              <FilePreview
                fileName={file.name}
                mimeType={file.mimeType}
                presignedUrl={presignedUrl}
                isUrlLoading={isUrlLoading}
              />
            </div>
          </div>

          {/* Versions */}
          {file.versions && file.versions.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 mt-6">
              <div className="px-5 py-4 border-b border-gray-200">
                <h2 className="text-sm font-medium text-gray-700 flex items-center gap-2">
                  <Clock size={16} />
                  Versions ({file.versions.length})
                </h2>
              </div>
              <div className="divide-y divide-gray-100">
                {file.versions.map((version) => (
                  <div
                    key={version.id}
                    className="flex items-center justify-between px-5 py-3"
                  >
                    <div>
                      <p className="text-sm font-medium text-gray-900">
                        Version {version.versionNumber}
                      </p>
                      <p className="text-xs text-gray-500">
                        {formatFileSize(version.size)} &middot;{" "}
                        {formatRelativeTime(version.createdAt)} &middot;{" "}
                        {version.uploadedBy}
                      </p>
                    </div>
                    <a
                      href={version.downloadUrl}
                      className="text-sm text-otter-600 hover:underline"
                    >
                      Download
                    </a>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Sidebar info */}
        <div className="space-y-6">
          {/* File info */}
          <div className="bg-white rounded-xl border border-gray-200">
            <div className="px-5 py-4 border-b border-gray-200">
              <h2 className="text-sm font-medium text-gray-700">Details</h2>
            </div>
            <div className="p-5 space-y-4">
              <InfoRow icon={User} label="Owner" value={file.ownerName || "You"} />
              <InfoRow
                icon={HardDrive}
                label="Size"
                value={formatFileSize(file.size)}
              />
              <InfoRow
                icon={FileText}
                label="Type"
                value={file.mimeType}
              />
              <InfoRow
                icon={Clock}
                label="Modified"
                value={formatRelativeTime(file.updatedAt)}
              />
              <InfoRow
                icon={Clock}
                label="Created"
                value={formatRelativeTime(file.createdAt)}
              />
              {file.path && (
                <InfoRow
                  icon={File}
                  label="Path"
                  value={file.path}
                />
              )}
            </div>
          </div>

          {/* Shared with */}
          <div className="bg-white rounded-xl border border-gray-200">
            <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
              <h2 className="text-sm font-medium text-gray-700">Shared with</h2>
              <button className="text-xs text-otter-600 hover:underline">
                Manage
              </button>
            </div>
            <div className="p-5">
              {file.sharedWith.length === 0 ? (
                <p className="text-sm text-gray-400">Not shared with anyone</p>
              ) : (
                <div className="space-y-3">
                  {file.sharedWith.map((shared) => {
                    const resolved = resolvedUsers[shared.userId];
                    const displayName = resolved?.name || shared.name || shared.userId.slice(0, 8);
                    const displayEmail = resolved?.email || shared.email;
                    return (
                      <div key={shared.userId} className="flex items-center gap-3">
                        <div
                          className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white"
                          style={{ backgroundColor: generateColor(shared.userId) }}
                        >
                          {getInitials(displayName)}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-gray-900 truncate">
                            {displayName}
                          </p>
                          <p className="text-xs text-gray-500">
                            {displayEmail ? `${displayEmail} · ${shared.permission}` : shared.permission}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* Tags */}
          {file.tags.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200">
              <div className="px-5 py-4 border-b border-gray-200">
                <h2 className="text-sm font-medium text-gray-700">Tags</h2>
              </div>
              <div className="p-5 flex flex-wrap gap-2">
                {file.tags.map((tag) => (
                  <span
                    key={tag}
                    className="px-2.5 py-1 bg-gray-100 text-gray-600 rounded-full text-xs"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {showShareDialog && (
        <ShareDialog
          fileId={file.id}
          fileName={file.name}
          ownerId={file.ownerId}
          ownerName={file.ownerName || undefined}
          sharedWith={file.sharedWith}
          resolvedUsers={resolvedUsers}
          onShare={handleShare}
          onPermissionChange={async (userId, permission) => {
            await filesApi.updateSharePermission(file.id, userId, permission);
            queryClient.invalidateQueries({ queryKey: ["files", fileId] });
          }}
          onRemoveAccess={async (userId) => {
            await filesApi.removeShare(file.id, userId);
            queryClient.invalidateQueries({ queryKey: ["files", fileId] });
          }}
          onClose={() => setShowShareDialog(false)}
        />
      )}
    </div>
  );
}

function InfoRow({
  icon: Icon,
  label,
  value,
}: Readonly<{
  icon: typeof User;
  label: string;
  value: string;
}>) {
  return (
    <div className="flex items-start gap-3">
      <Icon size={16} className="text-gray-400 mt-0.5" />
      <div>
        <p className="text-xs text-gray-400">{label}</p>
        <p className="text-sm text-gray-700 break-all">{value}</p>
      </div>
    </div>
  );
}

function FileIcon({ mimeType }: Readonly<{ mimeType: string }>) {
  if (mimeType.startsWith("image/"))
    return <ImageIcon size={24} className="text-otter-600" />;
  if (mimeType.startsWith("video/"))
    return <Film size={24} className="text-otter-600" />;
  if (mimeType === "application/pdf")
    return <FileText size={24} className="text-red-500" />;
  return <File size={24} className="text-otter-600" />;
}


