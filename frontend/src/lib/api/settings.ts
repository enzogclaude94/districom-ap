import { apiFetch } from "@/lib/api/client";

export interface Setting {
  key: string;
  label: string;
  description: string;
  value: unknown;
  is_default: boolean;
}

/** Parameters of one scope: `platform` (general) or a module key. */
export interface SettingsScope {
  scope: string;
  label: string;
  settings: Setting[];
}

export const settingsApi = {
  list: () => apiFetch<SettingsScope[]>("/settings"),
};
