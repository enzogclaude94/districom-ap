import { apiFetch } from "@/lib/api/client";
import type { Page } from "@/lib/api/imports";

/**
 * Comercial › Datos › Maestros (F1.7) over the existing endpoints of `/api/v1/commercial`. `viewer` reads, a module
 * admin edits through the same services as before; every edit sends the `updated_at` it read
 * (`expected_updated_at`) so a save over a newer version is a 409 `changed_meanwhile`, never a silent overwrite.
 * Customers, products, zones, document types and reasons are created only by imports; nothing here creates them.
 */

export interface ZoneRef {
  id: number;
  name: string;
}

export interface SellerRef {
  salesperson_id: number | null;
  salesperson_name: string | null;
  source_label: string | null;
  mapping_id: number | null;
  valid_from: string;
}

/** A customer. A viewer's response has no `tax_id`, `address` nor `phone` keys at all. */
export interface Customer {
  id: number;
  display_name: string;
  legal_name: string | null;
  is_generic: boolean;
  active: boolean;
  last_seen_on: string | null;
  created_at: string;
  updated_at: string;
  store_sys_code: string | null;
  zone: ZoneRef | null;
  salesperson: SellerRef | null;
  tax_id?: string | null;
  address?: string | null;
  phone?: string | null;
}

export interface CustomerFilters {
  q?: string;
  active?: boolean;
  include_inactive?: boolean;
  is_generic?: boolean;
  zone_id?: number;
  salesperson_id?: number;
  limit: number;
  offset: number;
}

export interface CustomerHistory {
  customer_id: number;
  mappings: {
    id: number;
    source_system: string;
    namespace: string;
    kind_label: string | null;
    external_id: string;
    status: string;
    method: string | null;
  }[];
  zones: {
    id: number;
    zone: ZoneRef;
    valid_from: string;
    valid_to: string | null;
    voided: boolean;
    source: string;
    import_job_id: number | null;
  }[];
  salespeople: {
    id: number;
    salesperson_id: number | null;
    salesperson_name: string | null;
    source_label: string | null;
    mapping_id: number | null;
    valid_from: string;
    valid_to: string | null;
    voided: boolean;
    source: string;
    import_job_id: number | null;
  }[];
}

export interface Product {
  id: number;
  source_code: string;
  source_description: string | null;
  source_category: string | null;
  source_line: string | null;
  source_capacity: string | null;
  source_pack: string | null;
  canonical_category_id: number | null;
  category_origin: string;
  product_kind: string;
  analytics_eligible: boolean;
  eligibility_origin: string;
  family_id: number | null;
  active: boolean;
  created_at: string;
  updated_at: string;
  master_price: string | null;
}

export interface ProductDetail extends Product {
  prices: {
    id: number;
    list_number: number;
    price_list_name: string;
    price: string;
    valid_from: string;
    valid_to: string | null;
    voided: boolean;
  }[];
}

export interface ProductFilters {
  q?: string;
  category_id?: number;
  family_id?: number;
  has_category?: boolean;
  analytics_eligible?: boolean;
  active?: boolean;
  limit: number;
  offset: number;
}

export interface ProductChanges {
  category_id?: number | null;
  product_kind?: string;
  analytics_eligible?: boolean;
  family_id?: number | null;
  active?: boolean;
}

export interface Category {
  id: number;
  code: string;
  name: string;
  default_product_kind: string | null;
  default_analytics_eligible: boolean;
  parent_id: number | null;
  sort_order: number;
  active: boolean;
  updated_at: string;
}

export interface Family {
  id: number;
  code: string;
  name: string;
  description: string | null;
  active: boolean;
  updated_at: string;
}

export interface Zone {
  id: number;
  source_name: string;
  display_name: string;
  zone_type: string;
  requires_visit: boolean;
  weekdays: number[];
  configuration_origin: string;
  rule_id: number | null;
  active: boolean;
  configured_by: number | null;
  configured_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ZoneDetail extends Zone {
  visit_days: {
    id: number;
    weekday: number;
    valid_from: string;
    valid_to: string | null;
    voided_at: string | null;
    created_by: number | null;
  }[];
}

export interface ZoneConfiguration {
  zone_type: string;
  requires_visit: boolean;
  weekdays: number[];
  effective_from: string;
  display_name?: string;
  active?: boolean;
}

export interface ZoneRule {
  id: number;
  priority: number;
  match_type: string;
  pattern: string;
  zone_type: string;
  requires_visit: boolean;
  weekdays: number[];
  active: boolean;
  description: string | null;
  updated_at: string;
}

export interface EmployeeMapping {
  id: number;
  source_system: string;
  namespace: string;
  kind_label: string | null;
  external_id: string;
  external_label: string | null;
  status: string;
  customers: number;
}

export interface Salesperson {
  id: number;
  name: string;
  active: boolean;
  is_salesperson: boolean;
  mappings: EmployeeMapping[];
  customers: number;
}

export interface PriceList {
  id: number;
  list_number: number;
  code: string;
  name: string;
  description: string | null;
  kind: string;
  commercially_relevant: boolean;
  active: boolean;
  updated_at: string;
}

export interface DocumentType {
  id: number;
  source_name: string;
  document_class: string;
  sign: number;
  commercial_universe: boolean;
  active: boolean;
  decided_by: number | null;
  decided_at: string | null;
  notes: string | null;
  updated_at: string;
}

export interface ReasonCategory {
  id: number;
  code: string;
  name: string;
  active: boolean;
  updated_at: string;
}

export interface Reason {
  id: number;
  source_system_id: number;
  reason_source: string;
  category_id: number | null;
  decided_by: number | null;
  decided_at: string | null;
  updated_at: string;
}

/** What every edit carries: why (audited) and the version it was made on. */
export interface Edit {
  reason: string;
  expected_updated_at?: string;
}

function query(params: Record<string, string | number | boolean | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params))
    if (value !== undefined && value !== "") search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : "";
}

const base = "/commercial";
const send = <T>(method: string, path: string, body: unknown) =>
  apiFetch<T>(`${base}${path}`, { method, body: JSON.stringify(body) });

export const mastersApi = {
  customers: (filters: CustomerFilters) => apiFetch<Page<Customer>>(`${base}/customers${query({ ...filters })}`),
  customer: (id: number) => apiFetch<Customer>(`${base}/customers/${id}`),
  customerHistory: (id: number) => apiFetch<CustomerHistory>(`${base}/customers/${id}/history`),
  setCustomerFlags: (id: number, body: { is_generic?: boolean; active?: boolean } & Edit) =>
    send<Customer>("PATCH", `/customers/${id}`, body),

  products: (filters: ProductFilters) => apiFetch<Page<Product>>(`${base}/products${query({ ...filters })}`),
  product: (id: number) => apiFetch<ProductDetail>(`${base}/products/${id}`),
  updateProduct: (id: number, body: ProductChanges & Edit) => send<ProductDetail>("PATCH", `/products/${id}`, body),

  categories: () => apiFetch<Category[]>(`${base}/product-categories`),
  createCategory: (body: {
    code: string;
    name: string;
    default_product_kind: string | null;
    default_analytics_eligible: boolean;
    parent_id: number | null;
    sort_order: number;
  }) => send<Category>("POST", "/product-categories", body),
  updateCategory: (
    id: number,
    body: Partial<
      Pick<
        Category,
        "name" | "default_product_kind" | "default_analytics_eligible" | "parent_id" | "sort_order" | "active"
      >
    > &
      Edit,
  ) => send<Category>("PATCH", `/product-categories/${id}`, body),

  families: () => apiFetch<Family[]>(`${base}/product-families`),
  createFamily: (body: { code: string; name: string; description: string | null }) =>
    send<Family>("POST", "/product-families", body),
  updateFamily: (id: number, body: Partial<Pick<Family, "name" | "description" | "active">> & Edit) =>
    send<Family>("PATCH", `/product-families/${id}`, body),

  zones: () => apiFetch<Zone[]>(`${base}/zones`),
  zone: (id: number) => apiFetch<ZoneDetail>(`${base}/zones/${id}`),
  configureZone: (id: number, body: ZoneConfiguration & Edit) =>
    send<ZoneDetail>("PUT", `/zones/${id}/configuration`, body),
  zoneRules: () => apiFetch<ZoneRule[]>(`${base}/zone-rules`),
  createZoneRule: (
    body: Pick<ZoneRule, "priority" | "pattern" | "zone_type" | "requires_visit" | "weekdays" | "description">,
  ) => send<ZoneRule>("POST", "/zone-rules", body),
  updateZoneRule: (
    id: number,
    body: Partial<Pick<ZoneRule, "priority" | "zone_type" | "requires_visit" | "weekdays" | "active" | "description">> &
      Edit,
  ) => send<ZoneRule>("PATCH", `/zone-rules/${id}`, body),

  salespeople: () => apiFetch<{ items: Salesperson[]; unlinked: EmployeeMapping[] }>(`${base}/salespeople`),

  priceLists: () => apiFetch<PriceList[]>(`${base}/price-lists`),
  createPriceList: (body: Pick<PriceList, "list_number" | "name" | "kind" | "commercially_relevant" | "description">) =>
    send<PriceList>("POST", "/price-lists", body),
  updatePriceList: (
    id: number,
    body: Partial<Pick<PriceList, "name" | "kind" | "commercially_relevant" | "active" | "description">> & Edit,
  ) => send<PriceList>("PATCH", `/price-lists/${id}`, body),
  articlesPriceList: () => apiFetch<{ list_number: number | null }>(`${base}/price-lists/articles-master`),
  setArticlesPriceList: (body: { list_number: number | null; reason: string }) =>
    send<{ list_number: number | null }>("PUT", "/price-lists/articles-master", body),

  documentTypes: () => apiFetch<DocumentType[]>(`${base}/document-types`),
  classifyDocumentType: (
    id: number,
    body: { document_class: string; sign: number; commercial_universe: boolean } & Edit,
  ) => send<DocumentType>("PUT", `/document-types/${id}/classification`, body),

  reasonCategories: () => apiFetch<ReasonCategory[]>(`${base}/no-purchase-reason-categories`),
  createReasonCategory: (body: { code: string; name: string }) =>
    send<ReasonCategory>("POST", "/no-purchase-reason-categories", body),
  updateReasonCategory: (id: number, body: { name?: string; active?: boolean } & Edit) =>
    send<ReasonCategory>("PATCH", `/no-purchase-reason-categories/${id}`, body),
  reasons: () => apiFetch<Reason[]>(`${base}/no-purchase-reasons`),
  setReasonCategory: (id: number, body: { category_id: number | null } & Edit) =>
    send<Reason>("PUT", `/no-purchase-reasons/${id}/category`, body),
};

export const mastersQueryKey = ["commercial", "masters"] as const;
