import { useEffect, useState } from "react";
import { ApiError } from "@/api/client";
import { login as apiLogin, logout as apiLogout, me, type User } from "@/api/auth";

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    me()
      .then(setUser)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) setUser(null);
        else throw err;
      })
      .finally(() => setLoading(false));
  }, []);

  const login = async (email: string, password: string) => {
    const u = await apiLogin(email, password);
    setUser(u);
    return u;
  };

  const logout = async () => {
    await apiLogout();
    setUser(null);
  };

  return { user, loading, login, logout };
}
