import { api } from "./client";

export type User = { id: string; email: string };

export const login = (email: string, password: string) =>
  api<User>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });

export const logout = () => api<void>("/api/auth/logout", { method: "POST" });

export const me = () => api<User>("/api/auth/me");
