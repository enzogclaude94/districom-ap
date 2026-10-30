import type { AccessOrigin, ModuleRole, Role } from "@/lib/api/auth";
import { apiFetch } from "@/lib/api/client";

/** A role the user was explicitly given in a module or a protected area. */
export interface Grant {
  key: string;
  name: string;
  role: ModuleRole;
}

export interface UserSummary {
  id: number;
  email: string;
  full_name: string;
  /** Platform role: `admin` is admin in every module without grants; protected areas are never implicit. */
  role: Role;
  active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  password_changed_at: string | null;
  created_at: string;
  modules: Grant[];
  areas: Grant[];
  open_sessions: number;
}

export interface UserSession {
  id: number;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip: string | null;
  access_origin: AccessOrigin | null;
  user_agent: string | null;
}

export interface UserActivity {
  occurred_at: string;
  event_type: string;
  actor_id: number | null;
  actor_name: string | null;
  payload: Record<string, unknown>;
}

export interface UserDetail extends UserSummary {
  sessions: UserSession[];
  activity: UserActivity[];
}

/** The temporary password comes only in this response: the server keeps its hash and never shows it again. */
export interface TemporaryPassword {
  user: UserSummary;
  temporary_password: string;
  expires_at: string;
}

export interface AccessCatalog {
  modules: { key: string; name: string }[];
  areas: { key: string; name: string; module_key: string }[];
}

export type UserStatusFilter = "active" | "inactive" | "all";

export interface UserFilters {
  status: UserStatusFilter;
  q?: string;
  limit?: number;
  offset?: number;
}

export interface UserInput {
  email: string;
  full_name: string;
  role: Role;
}

function query(filters: UserFilters) {
  const params = new URLSearchParams({ status: filters.status });
  if (filters.q) params.set("q", filters.q);
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters.offset !== undefined) params.set("offset", String(filters.offset));
  return params.toString();
}

export const usersQueryKey = ["users"] as const;

export const usersApi = {
  list: (filters: UserFilters) => apiFetch<{ items: UserSummary[]; total: number }>(`/users?${query(filters)}`),
  get: (id: number) => apiFetch<UserDetail>(`/users/${id}`),
  catalog: () => apiFetch<AccessCatalog>("/users/access-catalog"),
  create: (input: UserInput) => apiFetch<TemporaryPassword>("/users", { method: "POST", body: JSON.stringify(input) }),
  update: (id: number, input: Partial<UserInput>) =>
    apiFetch<UserDetail>(`/users/${id}`, { method: "PATCH", body: JSON.stringify(input) }),
  deactivate: (id: number) => apiFetch<UserDetail>(`/users/${id}/deactivate`, { method: "POST" }),
  activate: (id: number) => apiFetch<UserDetail>(`/users/${id}/activate`, { method: "POST" }),
  resetPassword: (id: number) => apiFetch<TemporaryPassword>(`/users/${id}/reset-password`, { method: "POST" }),
  setAccess: (id: number, key: string, role: ModuleRole | null) =>
    apiFetch<UserDetail>(`/users/${id}/access`, { method: "PUT", body: JSON.stringify({ key, role }) }),
  revokeSession: (id: number, sessionId: number) =>
    apiFetch<{ revoked: number }>(`/users/${id}/sessions/${sessionId}/revoke`, { method: "POST" }),
  revokeAllSessions: (id: number) =>
    apiFetch<{ revoked: number }>(`/users/${id}/sessions/revoke-all`, { method: "POST" }),
};
