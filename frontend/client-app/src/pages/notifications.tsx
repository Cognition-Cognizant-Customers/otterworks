import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  Share2,
  MessageSquare,
  AtSign,
  Edit3,
  Info,
  CheckCheck,
} from "lucide-react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/layout/app-shell";
import { PageLoader } from "@/components/ui/loading-spinner";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { notificationsApi } from "@/lib/api";
import { formatRelativeTime, cn, getInitials, generateColor } from "@/lib/utils";
import type { Notification } from "@/types";

export default function NotificationsPage() {
  return (
    <AppShell>
      <ErrorBoundary>
        <NotificationsContent />
      </ErrorBoundary>
    </AppShell>
  );
}

function NotificationsContent() {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => notificationsApi.list(),
  });

  const markReadMutation = useMutation({
    mutationFn: notificationsApi.markRead,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  const markAllReadMutation = useMutation({
    mutationFn: notificationsApi.markAllRead,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  const notifications = data?.data || [];
  const unreadCount = notifications.filter((n) => !n.read).length;

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Notifications</h1>
          {unreadCount > 0 && (
            <p className="text-sm text-gray-500 mt-1">
              {unreadCount} unread notification{unreadCount !== 1 ? "s" : ""}
            </p>
          )}
        </div>
        {unreadCount > 0 && (
          <button
            onClick={() => markAllReadMutation.mutate()}
            disabled={markAllReadMutation.isPending}
            className="flex items-center gap-2 px-3 py-2 text-sm text-otter-600 bg-otter-50 rounded-lg hover:bg-otter-100 transition"
          >
            <CheckCheck size={16} />
            Mark all as read
          </button>
        )}
      </div>

      {/* Notifications */}
      {isLoading ? (
        <PageLoader />
      ) : notifications.length === 0 ? (
        <EmptyState
          icon={Bell}
          title="No notifications"
          description="You&apos;re all caught up! New notifications will appear here."
        />
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 divide-y divide-gray-100 overflow-hidden">
          {notifications.map((notification) => (
            <NotificationRow
              key={notification.id}
              notification={notification}
              onMarkRead={() => markReadMutation.mutate(notification.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

const notificationIcons: Record<string, typeof Bell> = {
  share: Share2,
  comment: MessageSquare,
  mention: AtSign,
  edit: Edit3,
  system: Info,
};

const notificationColors: Record<string, string> = {
  share: "text-purple-600 bg-purple-50",
  comment: "text-orange-600 bg-orange-50",
  mention: "text-blue-600 bg-blue-50",
  edit: "text-green-600 bg-green-50",
  system: "text-gray-600 bg-gray-100",
};

function NotificationRow({
  notification,
  onMarkRead,
}: Readonly<{
  notification: Notification;
  onMarkRead: () => void;
}>) {
  const Icon = notificationIcons[notification.type] || Bell;
  const color = notificationColors[notification.type] || "text-gray-600 bg-gray-100";

  const href =
    notification.resourceId && notification.resourceType === "document"
      ? `/documents/${notification.resourceId}`
      : notification.resourceId && notification.resourceType === "file"
      ? `/files/${notification.resourceId}`
      : undefined;

  const handleActivate = () => {
    if (!notification.read) onMarkRead();
  };

  const rowClassName = cn(
    "flex w-full items-start gap-4 px-5 py-4 text-left transition cursor-pointer",
    !notification.read ? "bg-otter-50/30" : "hover:bg-gray-50"
  );

  const content = (
    <>
      {notification.actorName ? (
        <div
          className="w-10 h-10 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0"
          style={{
            backgroundColor: generateColor(notification.actorId || notification.actorName),
          }}
        >
          {getInitials(notification.actorName)}
        </div>
      ) : (
        <div
          className={cn(
            "w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0",
            color
          )}
        >
          <Icon size={18} />
        </div>
      )}
      <div className="flex-1 min-w-0">
        <p className="text-sm text-gray-900">
          <span className="font-medium">{notification.title}</span>
        </p>
        <p className="text-sm text-gray-500 mt-0.5">{notification.message}</p>
        <p className="text-xs text-gray-400 mt-1">
          {formatRelativeTime(notification.createdAt)}
        </p>
      </div>
      {!notification.read && (
        <div className="w-2.5 h-2.5 rounded-full bg-otter-600 flex-shrink-0 mt-1.5" />
      )}
    </>
  );

  if (href) {
    return (
      <Link to={href} className={rowClassName} onClick={handleActivate}>
        {content}
      </Link>
    );
  }

  return (
    <button type="button" className={rowClassName} onClick={handleActivate}>
      {content}
    </button>
  );
}
