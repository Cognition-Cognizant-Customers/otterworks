import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { RequireAuth } from "./components/RequireAuth";
import { LoginPage } from "./pages/LoginPage";
import { IncidentsPage } from "./pages/IncidentsPage";
import { QuotasPage } from "./pages/QuotasPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/incidents" element={<IncidentsPage />} />
        <Route path="/quotas" element={<QuotasPage />} />
        <Route path="/" element={<Navigate to="/incidents" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
