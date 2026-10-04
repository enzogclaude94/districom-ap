import { apiFetch } from "@/lib/api/client";

/** Platform role: `admin` administers the platform and is admin in every module. */
export type Role = "admin" | "viewer";
/** Role inside one module (ARCHITECTURE.md §8). */
export type ModuleRole = "viewer" | "operator" | "admin";

export interface ModuleAccess {
  key: string;
  name: string;
  role: ModuleRole;
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
