import { apiFetch } from "@/lib/api/client";

export type Role = "admin" | "viewer";

export interface ModuleAccess {
  key: string;
  name: string;
  role: Role;
}

export interface CurrentUser {
  id: number;
  email: string;
  full_name: string;
  /** Platform role. Per-module access is in `modules`. */
  role: Role;
  modules: ModuleAccess[];
}

export const authApi = {
  login: (email: string, password: string) =>
    apiFetch<CurrentUser>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => apiFetch<void>("/auth/logout", { method: "POST" }),
  me: () => apiFetch<CurrentUser>("/auth/me"),
};

export const currentUserQueryKey = ["auth", "me"] as const;
