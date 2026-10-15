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
  /** Protected areas the user was granted (ADR 010), e.g. `settlements.employee_finance`. */
  areas: ModuleAccess[];
}

export const authApi = {
  login: (email: string, password: string) =>
    apiFetch<CurrentUser>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => apiFetch<void>("/auth/logout", { method: "POST" }),
  me: () => apiFetch<CurrentUser>("/auth/me"),
};

export const currentUserQueryKey = ["auth", "me"] as const;

const MODULE_ROLE_RANK: Record<ModuleRole, number> = { viewer: 0, operator: 1, admin: 2 };

/** The user's role in a module: the platform admin is admin in every module (ADR 007, D1). */
export function moduleRole(user: CurrentUser | undefined, moduleKey: string): ModuleRole | null {
  if (!user) return null;
  if (user.role === "admin") return "admin";
  return user.modules.find((module) => module.key === moduleKey)?.role ?? null;
}

/**
 * The user's role in a protected area (ADR 010): only an explicit grant counts, the platform admin included.
 * Only hides UI: without the area the API answers 404.
 */
export function areaRole(user: CurrentUser | undefined, areaKey: string): ModuleRole | null {
  return user?.areas?.find((area) => area.key === areaKey)?.role ?? null;
}

/** Whether `role` reaches `required` (`viewer < operator < admin`). Only hides UI: the API decides. */
export function roleAtLeast(role: ModuleRole | null, required: ModuleRole) {
  return role !== null && MODULE_ROLE_RANK[role] >= MODULE_ROLE_RANK[required];
}
