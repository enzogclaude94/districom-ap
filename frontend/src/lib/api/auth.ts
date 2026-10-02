import { apiFetch } from "@/lib/api/client";

export type Role = "admin" | "viewer";

export interface CurrentUser {
  id: number;
  email: string;
  full_name: string;
  role: Role;
}

export const authApi = {
  login: (email: string, password: string) =>
    apiFetch<CurrentUser>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => apiFetch<void>("/auth/logout", { method: "POST" }),
  me: () => apiFetch<CurrentUser>("/auth/me"),
};

export const currentUserQueryKey = ["auth", "me"] as const;
