import { Link, useLocation } from "react-router-dom";
import {
  Home,
  Clock,
  FolderOpen,
  FileText,
  Search,
  Bell,
  Settings,
  Share2,
  Trash2,
  Star,
  TrendingUp,
  Menu,
  X,
  Plus,
  LogOut,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/stores/ui-store";
import { useAuthStore } from "@/stores/auth-store";
import { getInitials } from "@/lib/utils";
import { Logo } from "@/components/ui/logo";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const navGroups: NavGroup[] = [
  {
    label: "Workspace",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: Home },
      { href: "/recent", label: "Recent", icon: Clock },
      { href: "/files", label: "Files", icon: FolderOpen },
      { href: "/documents", label: "Documents", icon: FileText },
      { href: "/search", label: "Search", icon: Search },
      { href: "/margins", label: "Margins", icon: TrendingUp },
    ],
  },
  {
    label: "Collaboration",
    items: [
      { href: "/shared", label: "Shared with me", icon: Share2 },
      { href: "/starred", label: "Starred", icon: Star },
      { href: "/notifications", label: "Notifications", icon: Bell },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/trash", label: "Trash", icon: Trash2 },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

export function Sidebar() {
  const { pathname } = useLocation();
  const { sidebarOpen, toggleSidebar } = useUIStore();
  const { user, logout } = useAuthStore();

  return (
    <>
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={toggleSidebar}
        />
      )}

      <aside
        className={cn(
          "fixed top-0 left-0 z-50 h-full bg-otter-700 text-white border-r border-otter-800 transition-transform duration-200 flex flex-col",
          "w-64",
          sidebarOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0 lg:w-16"
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between h-12 px-3 border-b border-otter-600">
          {sidebarOpen && (
            <Link to="/dashboard" className="flex items-center gap-2">
              <Logo size={28} className="rounded-sm bg-white" />
              <span className="font-semibold text-white text-sm">OtterWorks</span>
            </Link>
          )}
          <button
            onClick={toggleSidebar}
            className="p-2 rounded hover:bg-otter-600 text-otter-200"
            aria-label="Toggle sidebar"
          >
            {sidebarOpen ? <X size={18} /> : <Menu size={18} />}
          </button>
        </div>

        {/* New button */}
        {sidebarOpen && (
          <div className="p-3">
            <Link
              to="/files"
              className="flex items-center justify-center gap-2 w-full px-4 py-2 bg-accent-500 text-white rounded hover:bg-accent-600 transition font-medium text-sm"
            >
              <Plus size={16} />
              New
            </Link>
          </div>
        )}

        {/* Navigation */}
        <nav className="flex-1 px-2 py-1 overflow-y-auto">
          {navGroups.map((group) => (
            <div key={group.label} className="mb-2">
              {sidebarOpen && (
                <p className="px-3 pt-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-otter-300">
                  {group.label}
                </p>
              )}
              <ul className="space-y-0.5">
                {group.items.map((item) => {
                  const isActive =
                    pathname === item.href || pathname.startsWith(item.href + "/");
                  return (
                    <li key={item.href}>
                      <Link
                        to={item.href}
                        className={cn(
                          "flex items-center gap-3 px-3 py-1.5 rounded-sm text-sm font-medium transition border-l-2",
                          isActive
                            ? "bg-otter-600 text-white border-accent-500"
                            : "text-otter-100 border-transparent hover:bg-otter-600 hover:text-white"
                        )}
                      >
                        <item.icon size={18} />
                        {sidebarOpen && <span>{item.label}</span>}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        {/* User profile */}
        <div className="border-t border-otter-600 px-3 py-2">
          {user && sidebarOpen && (
            <div className="flex items-center gap-3 px-1 py-2">
              <div className="w-8 h-8 rounded-full bg-otter-500 text-white flex items-center justify-center text-xs font-semibold">
                {getInitials(user.displayName)}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-white truncate">
                  {user.displayName}
                </p>
                <p className="text-xs text-otter-200 truncate">{user.email}</p>
              </div>
              <button
                onClick={logout}
                className="p-1.5 rounded hover:bg-otter-600 text-otter-200 hover:text-white"
                aria-label="Sign out"
              >
                <LogOut size={16} />
              </button>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
