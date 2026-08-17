import { create } from "zustand";
import { authApi } from "@/lib/api";
import type { User } from "@/types";

interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  setUser: (user: User | null) => void;
  setLoading: (loading: boolean) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: false,
  isLoading: true,
  setUser: (user) => set({ user, isAuthenticated: !!user, isLoading: false }),
  setLoading: (isLoading) => set({ isLoading }),
  logout: () => {
    authApi.logout().catch(() => {});
    if (typeof globalThis.window !== "undefined") {
      localStorage.removeItem("otter_access_token");
      localStorage.removeItem("otter_refresh_token");
    }
    set({ user: null, isAuthenticated: false, isLoading: false });
    if (typeof globalThis.window !== "undefined") {
      globalThis.location.href = "/login";
    }
  },
}));
