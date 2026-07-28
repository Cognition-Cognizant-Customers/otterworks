import { Link, Route, Routes } from "react-router-dom";
import { Toaster } from "react-hot-toast";
import { QueryProvider } from "@/providers/query-provider";
import { AuthProvider } from "@/providers/auth-provider";
import Home from "@/pages/landing";
import DashboardPage from "@/pages/dashboard";
import DocumentsPage from "@/pages/documents";
import DocumentEditorPage from "@/pages/document-editor";
import FilesPage from "@/pages/files";
import FileDetailPage from "@/pages/file-detail";
import LoginPage from "@/pages/login";
import MarginsPage from "@/pages/margins";
import RegisterPage from "@/pages/register";
import NotificationsPage from "@/pages/notifications";
import RecentPage from "@/pages/recent";
import SearchPage from "@/pages/search";
import SettingsPage from "@/pages/settings";
import SharedPage from "@/pages/shared";
import StarredPage from "@/pages/starred";
import TrashPage from "@/pages/trash";
import TermsPage from "@/pages/terms";
import PrivacyPage from "@/pages/privacy";

function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4">
      <h1 className="text-2xl font-semibold text-gray-900">404 — Page not found</h1>
      <Link to="/" className="text-otter-600 hover:underline">
        Back to home
      </Link>
    </div>
  );
}

export default function App() {
  return (
    <QueryProvider>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/documents/:id" element={<DocumentEditorPage />} />
          <Route path="/files" element={<FilesPage />} />
          <Route path="/files/:id" element={<FileDetailPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/margins" element={<MarginsPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="/recent" element={<RecentPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/shared" element={<SharedPage />} />
          <Route path="/starred" element={<StarredPage />} />
          <Route path="/trash" element={<TrashPage />} />
          <Route path="/terms" element={<TermsPage />} />
          <Route path="/privacy" element={<PrivacyPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </AuthProvider>
      <Toaster
        position="bottom-right"
        toastOptions={{
          duration: 4000,
          style: {
            borderRadius: "12px",
            padding: "12px 16px",
            fontSize: "14px",
          },
          success: {
            iconTheme: { primary: "#16a34a", secondary: "#fff" },
          },
          error: {
            iconTheme: { primary: "#dc2626", secondary: "#fff" },
          },
        }}
      />
    </QueryProvider>
  );
}
