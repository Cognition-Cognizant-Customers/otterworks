import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { authApi } from "@/lib/api";
import { AuthProvider } from "@/providers/auth-provider";
import { useAuthStore } from "./auth-store";
import { useUIStore } from "./ui-store";

const user = {
  id: "user-1",
  email: "otter@example.com",
  displayName: "Otter User",
};

describe("auth store", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      isAuthenticated: false,
      isLoading: true,
    });
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("transitions between loading, authenticated, and signed-out states", () => {
    const { result } = renderHook(() => useAuthStore());
    act(() => result.current.setLoading(false));
    expect(result.current).toMatchObject({ isLoading: false, isAuthenticated: false });

    act(() => result.current.setUser(user));
    expect(result.current).toMatchObject({
      user,
      isAuthenticated: true,
      isLoading: false,
    });

    act(() => result.current.setUser(null));
    expect(result.current).toMatchObject({
      user: null,
      isAuthenticated: false,
      isLoading: false,
    });
  });

  it("rehydrates a token-backed session through AuthProvider", async () => {
    localStorage.setItem("otter_access_token", "access-token");
    const getProfile = vi.spyOn(authApi, "getProfile").mockResolvedValue(user);

    renderHook(() => useAuthStore(), { wrapper: AuthProvider });

    await waitFor(() => {
      expect(useAuthStore.getState()).toMatchObject({
        user,
        isAuthenticated: true,
        isLoading: false,
      });
    });
    expect(getProfile).toHaveBeenCalledOnce();
  });

  it("clears persisted tokens and state on logout", async () => {
    localStorage.setItem("otter_access_token", "access-token");
    localStorage.setItem("otter_refresh_token", "refresh-token");
    vi.spyOn(authApi, "logout").mockResolvedValue();
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { result } = renderHook(() => useAuthStore());
    act(() => result.current.setUser(user));

    act(() => result.current.logout());

    expect(localStorage.getItem("otter_access_token")).toBeNull();
    expect(localStorage.getItem("otter_refresh_token")).toBeNull();
    expect(result.current).toMatchObject({
      user: null,
      isAuthenticated: false,
      isLoading: false,
    });
  });
});

describe("ui store", () => {
  beforeEach(() => {
    useUIStore.setState({
      sidebarOpen: true,
      viewMode: "grid",
      sortConfig: { field: "updatedAt", direction: "desc" },
    });
  });

  it("toggles and explicitly controls navigation preferences", () => {
    const { result } = renderHook(() => useUIStore());
    act(() => result.current.toggleSidebar());
    expect(result.current.sidebarOpen).toBe(false);
    act(() => result.current.setSidebarOpen(true));
    act(() => result.current.setViewMode("list"));
    act(() => result.current.setSortConfig({ field: "size", direction: "asc" }));

    expect(result.current).toMatchObject({
      sidebarOpen: true,
      viewMode: "list",
      sortConfig: { field: "size", direction: "asc" },
    });
  });
});
