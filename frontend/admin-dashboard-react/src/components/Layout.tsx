import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { getCurrentUser, logout } from "@/lib/auth";

export function Layout() {
  const navigate = useNavigate();
  const user = getCurrentUser();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="material-icons" aria-hidden>
            water
          </span>
          <span>OtterWorks Admin</span>
        </div>
        <nav className="sidebar-nav">
          <NavLink to="/incidents" className={({ isActive }) => (isActive ? "active" : "")}>
            <span className="material-icons" aria-hidden>
              report_problem
            </span>
            Incidents
          </NavLink>
          <NavLink to="/quotas" className={({ isActive }) => (isActive ? "active" : "")}>
            <span className="material-icons" aria-hidden>
              data_usage
            </span>
            Storage Quotas
          </NavLink>
        </nav>
        <div className="sidebar-footer">
          <span className="sidebar-user">{user?.email}</span>
          <button
            type="button"
            className="btn btn-stroked"
            onClick={() => {
              logout();
              navigate("/login");
            }}
          >
            <span className="material-icons" aria-hidden>
              logout
            </span>
            Log out
          </button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
