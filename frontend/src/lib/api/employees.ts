import { apiFetch } from "@/lib/api/client";

export interface EmployeeFunction {
  id: number;
  function_code: string;
  label: string;
  valid_from: string;
  valid_to: string | null;
  source: string;
  current: boolean;
}

export interface Employee {
  id: number;
  full_name: string;
  active: boolean;
  hire_date: string | null;
  phone: string | null;
  external_axum_id: string | null;
  functions: EmployeeFunction[];
}

export interface EmployeePage {
  items: Employee[];
  total: number;
}

export interface EmployeeInput {
  full_name: string;
  hire_date: string | null;
  phone: string | null;
}

export interface FunctionCode {
  code: string;
  label: string;
}

export interface EmployeeFilters {
  q?: string;
  active?: boolean;
  limit?: number;
  offset?: number;
}

function query(filters: EmployeeFilters) {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.active !== undefined) params.set("active", String(filters.active));
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters.offset !== undefined) params.set("offset", String(filters.offset));
  const text = params.toString();
  return text ? `?${text}` : "";
}

const post = <T>(path: string, body?: unknown) =>
  apiFetch<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

/** Employee administration (platform admins). */
export const employeesApi = {
  list: (filters: EmployeeFilters = {}) => apiFetch<EmployeePage>(`/employees${query(filters)}`),
  get: (id: number) => apiFetch<Employee>(`/employees/${id}`),
  create: (input: EmployeeInput) => post<Employee>("/employees", input),
  update: (id: number, input: EmployeeInput) =>
    apiFetch<Employee>(`/employees/${id}`, { method: "PUT", body: JSON.stringify(input) }),
  deactivate: (id: number, endDate: string) => post<Employee>(`/employees/${id}/deactivate`, { end_date: endDate }),
  reactivate: (id: number) => post<Employee>(`/employees/${id}/reactivate`),
  assignFunction: (id: number, functionCode: string, validFrom: string) =>
    post<Employee>(`/employees/${id}/functions`, { function_code: functionCode, valid_from: validFrom }),
  endFunction: (id: number, functionId: number, validTo: string) =>
    post<Employee>(`/employees/${id}/functions/${functionId}/end`, { valid_to: validTo }),
  functionCodes: () => apiFetch<FunctionCode[]>("/employees/function-codes"),
};

export const employeesQueryKey = ["employees"] as const;
