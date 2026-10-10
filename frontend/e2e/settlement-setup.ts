import { expect, type APIRequestContext } from "@playwright/test";

/**
 * Setup shared by the cheque and payment specs, by API: the steps before the flow under test (a driver, a
 * closed settlement with a pending, a cheque collected for it).
 */

export const HEADERS = { "X-Requested-With": "e2e" };
const DRIVER_FUNCTION = "settlements.delivery_driver";

/** Days from today in the business calendar (the browser runs in the business time zone). */
export function day(offset: number) {
  const at = new Date(Date.now() + offset * 24 * 60 * 60 * 1000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Argentina/Buenos_Aires" }).format(at);
}

export async function api<T>(
  request: APIRequestContext,
  method: "GET" | "POST" | "PUT" | "PATCH",
  path: string,
  data?: unknown,
) {
  const response = await request.fetch(`/api/v1${path}`, { method, headers: HEADERS, data });
  expect(response.ok(), `${method} ${path}: ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

export async function driver(request: APIRequestContext, name: string) {
  const created = await api<{ id: number }>(request, "POST", "/employees", {
    full_name: name,
    hire_date: null,
    phone: null,
  });
  await api(request, "POST", `/employees/${created.id}/functions`, {
    function_code: DRIVER_FUNCTION,
    valid_from: "2026-01-01",
  });
  return created.id;
}

export async function openSettlement(request: APIRequestContext, driverId: number, amount: string) {
  const created = await api<{ id: number }>(request, "POST", "/settlements/deliveries", {
    client_request_id: crypto.randomUUID(),
    business_date: day(-1),
    employee_id: driverId,
    expected_amount: amount,
  });
  return created.id;
}

/** A closed settlement whose only justification is one pending (API: the setup is not under test). */
export async function closedWithPending(
  request: APIRequestContext,
  driverId: number,
  customer: string,
  amount: string,
) {
  const id = await openSettlement(request, driverId, amount);
  const loaded = await api<{ pending_collections: { id: number }[] }>(
    request,
    "POST",
    `/settlements/deliveries/${id}/pending-collections`,
    { client_request_id: crypto.randomUUID(), customer_name: customer, amount, reason: "Paga con cheque" },
  );
  const preview = await api<{ preview_token: string }>(request, "GET", `/settlements/deliveries/${id}/closing-preview`);
  await api(request, "POST", `/settlements/deliveries/${id}/close`, {
    version: 1,
    preview_token: preview.preview_token,
    allocations: [],
    confirm: true,
  });
  return loaded.pending_collections[0].id;
}

export async function galicia(request: APIRequestContext) {
  const banks = await api<{ id: number; bcra_code: number }[]>(request, "GET", "/financial-institutions");
  return banks.find((b) => b.bcra_code === 7)!.id;
}

/** A physical cheque collected for a pending, by API (setup of the flows that start later in the cycle). */
export async function chequeForPending(
  request: APIRequestContext,
  pendingId: number,
  amount: string,
  number: string,
  customer: string,
) {
  const detail = await api<{ receipts: { cheque_id: number | null }[] }>(
    request,
    "POST",
    `/settlements/pending-collections/${pendingId}/cheque-receipts`,
    {
      client_request_id: crypto.randomUUID(),
      business_date: day(-1),
      kind: "PHYSICAL",
      payment_type: "COMMON",
      amount,
      issuer_bank_id: await galicia(request),
      cheque_number: number,
      issue_date: day(-1),
      drawer_name: "Librador E2E",
      delivered_by_name: customer,
      physical_receipt_confirmed: true,
    },
  );
  return detail.receipts.find((r) => r.cheque_id !== null)!.cheque_id!;
}
