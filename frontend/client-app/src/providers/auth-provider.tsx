import { useEffect, type ReactNode } from "react";
import { useAuthStore } from "@/stores/auth-store";
import { authApi } from "@/lib/api";

export function AuthProvider({ children }: Readonly<{ children: ReactNode }>) {
  const { setUser, setLoading } = useAuthStore();

  useEffect(() => {
    const token = localStorage.getItem("otter_access_token");
    if (!token) {
      setUser(null);
      return;
    }

    setLoading(true);
    authApi
      .getProfile()
      .then((user) => setUser(user))
      .catch(() => setUser(null));
  }, [setUser, setLoading]);

  return <>{children}</>;
}
