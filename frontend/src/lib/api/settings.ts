import { apiFetch } from "@/lib/api/client";

export interface CommercialSetting {
  key: string;
  value: unknown;
  is_default: boolean;
}

export const settingsApi = {
  list: () => apiFetch<CommercialSetting[]>("/settings"),
};
