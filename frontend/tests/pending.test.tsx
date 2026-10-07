import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import type { Account } from "@/modules/settlements/cash/api";
import { SourceLink } from "@/modules/settlements/cash/source-link";
import { PendingAlertSettings } from "@/modules/settlements/config/pending-alert-settings";
import type { Settlement } from "@/modules/settlements/delivery/api";
import { SettlementDetail } from "@/modules/settlements/delivery/settlement-detail";
import { PendingAlertBadge, PendingAlertCard } from "@/modules/settlements/pending/alerts";
import type { PendingDetail, PendingItem, Pending, Receipt, Resolution } from "@/modules/settlements/pending/api";
import { pendingErrorView } from "@/modules/settlements/pending/errors";
import { STATE_LABELS, ageText } from "@/modules/settlements/pending/labels";
import { PendingDetailView } from "@/modules/settlements/pending/pending-detail";
import { PendingList } from "@/modules/settlements/pending/pending-list";

import { jsonResponse, renderWithQuery } from "./render";

const search = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => search.params,
}));

type Role = "viewer" | "operator" | "admin";
type Handler = (init: RequestInit | undefined, call: number, url: URL) => Response | Promise<Response>;

const me = (role: Role) => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "settlements", name: "Rendiciones", role }],
});

const account = (id: number, name: string, kind: Account["kind"], active = true): Account => ({
  id,
  code: null,
  name,
  kind,
  allow_negative: false,
  active,
  balance: "0.00",
});

const ACCOUNTS: Account[] = [
  account(1, "Caja principal", "CASH_BOX"),
  account(2, "Caja vieja", "CASH_BOX", false),
  account(3, "Banco", "BANK"),
  account(4, "Banco cerrado", "BANK", false),
  account(5, "Pendientes de cobro", "PENDING_COLLECTIONS"),
];

const EMPLOYEES = [
  { id: 4, full_name: "Pedro Gómez" },
  { id: 5, full_name: "Juan Ayudante" },
];

function pending(changes: Partial<Pending> = {}): Pending {
  return {
    id: 12,
    settlement_id: 7,
    customer_name: "Almacén Don José",
    amount: "100000.00",
    reason: "No tenía cambio",
    responsible_employee_id: 4,
    responsible_name: "Pedro Gómez",
    expected_date: null,
    created_by: 9,
    created_by_name: "Oscar Operador",
    created_at: "2026-10-04T10:00:00Z",
    updated_by: null,
    updated_by_name: null,
    updated_at: "2026-10-04T10:00:00Z",
    voided: false,
    voided_by: null,
    voided_by_name: null,
    voided_at: null,
    void_reason: null,
    state: "OPEN",
    amount_in_effect: "100000.00",
    customer_in_effect: "Almacén Don José",
    collected: "0.00",
    resolved: "0.00",
    balance: "100000.00",
    load_change: null,
    load_change_detail: null,
    age_hours: 5,
    alert: false,
    expected_date_overdue: false,
    ...changes,
  };
}

const item = (changes: Partial<PendingItem> = {}): PendingItem => ({
  ...pending(),
  settlement_business_date: "2026-10-04",
  settlement_sequence_number: 1,
  driver_id: 4,
  driver_name: "Pedro Gómez",
  ...changes,
});

const RECEIPT: Receipt = {
  id: 31,
  amount: "60000.00",
  business_date: "2026-10-05",
  method: "CASH",
  destination_account_id: 1,
  destination_name: "Caja principal",
  operation_id: 501,
  created_by: 9,
  created_by_name: "Oscar Operador",
  created_at: "2026-10-05T10:00:00Z",
  reversal: null,
};

const RESOLUTION: Resolution = {
  id: 41,
  amount: "20000.00",
  business_date: "2026-10-05",
  reason: "Cerró el local",
  allocations: [{ target: "COMPANY", employee_id: null, employee_name: null, amount: "20000.00" }],
  operation_id: 502,
  created_by: 1,
  created_by_name: "Ana Admin",
  created_at: "2026-10-05T11:00:00Z",
  reversal: null,
};

const detail = (changes: Partial<PendingDetail> = {}): PendingDetail => ({
  ...item(),
  settlement_financial_state: "CLOSED",
  published: [],
  receipts: [],
  resolutions: [],
  events: [
    {
      id: 1,
      event_type: "CREATED",
      settlement_version: 1,
      before: null,
      after: { amount: "100000.00", customer_name: "Almacén Don José" },
      reason: null,
      close_operation_id: null,
      receipt_id: null,
      resolution_id: null,
      created_by: 9,
      created_by_name: "Oscar Operador",
      created_at: "2026-10-04T10:00:00Z",
    },
    {
      id: 2,
      event_type: "PUBLISHED",
      settlement_version: 1,
      before: null,
      after: { amount: "100000.00" },
      reason: null,
      close_operation_id: 400,
      receipt_id: null,
      resolution_id: null,
      created_by: 9,
      created_by_name: "Oscar Operador",
      created_at: "2026-10-04T12:00:00Z",
    },
  ],
  ...changes,
});

function mockApi(routes: Record<string, Handler>) {
  const counts = new Map<string, number>();
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    const handler = routes[key];
    if (!handler) return jsonResponse(404, { code: "not_found", message: key, details: null, request_id: null });
    const call = (counts.get(key) ?? 0) + 1;
    counts.set(key, call);
    return handler(init, call, url);
  });
}

const base = (role: Role, current: PendingDetail = detail()): Record<string, Handler> => ({
  "GET /auth/me": () => jsonResponse(200, me(role)),
  "GET /settlements/accounts": () => jsonResponse(200, ACCOUNTS),
  "GET /settlements/employees": () => jsonResponse(200, EMPLOYEES),
  "GET /settlements/pending-collections/12": () => jsonResponse(200, current),
  "GET /settlements/pending-collections/summary": () =>
    jsonResponse(200, { alert_hours: 24, count: 0, balance: "0.00" }),
});

type Spy = ReturnType<typeof mockApi>;
const calls = (spy: Spy, method: string, path: string) =>
  spy.mock.calls.filter(
    ([input, init]) =>
      new URL(String(input), "http://localhost").pathname === `/api/v1${path}` && (init?.method ?? "GET") === method,
  );
const bodies = (spy: Spy, method: string, path: string) =>
  calls(spy, method, path).map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);
const apiError = (status: number, code: string, details: unknown = null) =>
  jsonResponse(status, { code, message: "Mensaje del backend.", details, request_id: "req-1" });

describe("labels and errors", () => {
  it("names states for people, ages in hours or days, and maps every pending error without codes", () => {
    expect(STATE_LABELS).toEqual({
      DRAFT: "En rendición abierta",
      OPEN: "Pendiente",
      PARTIAL: "Parcial",
      COLLECTED: "Cobrado",
      RESOLVED: "Resuelto",
      VOIDED: "Anulado",
    });
    expect([ageText(5), ageText(30), ageText(72)]).toEqual(["5 h", "1 día", "3 días"]);
    const codes = [
      "pending_not_in_effect",
      "pending_amount_exceeds_balance",
      "invalid_destination",
      "invalid_receipt_date",
      "future_business_date",
      "receipt_already_reversed",
      "resolution_allocation_mismatch",
      "resolution_already_reversed",
      "pending_has_activity",
      "pending_below_applied",
      "pending_customer_locked",
      "insufficient_funds",
      "idempotency_conflict",
    ];
    for (const code of codes) {
      const view = pendingErrorView(new ApiError(409, { code, message: "x", details: null, request_id: null }));
      expect(view.message, code).not.toContain(code);
      expect(view.message, code).not.toBe("x");
      expect(view.uncertain, code).toBeFalsy();
    }
    const exceeds = new ApiError(409, {
      code: "pending_amount_exceeds_balance",
      message: "x",
      details: { balance: "40000.00" },
      request_id: null,
    });
    expect(pendingErrorView(exceeds).message).toContain("$ 40.000,00");
    const funds = new ApiError(409, { code: "insufficient_funds", message: "x", details: null, request_id: null });
    expect(pendingErrorView(funds, true).message).toContain("dejaría inconsistente el saldo histórico");
    expect(pendingErrorView(new TypeError("fetch failed")).uncertain).toBe(true);
  });
});

describe("global list", () => {
  it("asks for what is owed by default and sends each filter to the backend", async () => {
    search.params = new URLSearchParams();
    const spy = mockApi({
      ...base("viewer"),
      "GET /settlements/pending-collections": () =>
        jsonResponse(200, {
          items: [
            item({
              state: "PARTIAL",
              collected: "60000.00",
              resolved: "20000.00",
              balance: "20000.00",
              alert: true,
              age_hours: 50,
              expected_date: "2026-10-05",
              expected_date_overdue: true,
            }),
          ],
          total: 1,
        }),
    });
    renderWithQuery(<PendingList />);
    const row = await screen.findByTestId("pending-item-12");
    expect(within(row).getByText("Parcial")).toBeInTheDocument();
    expect(row).toHaveTextContent("Cobrado: $ 60.000,00 · Resuelto: $ 20.000,00 · Saldo: $ 20.000,00");
    // Alert and overdue expected date are two separate marks.
    expect(within(row).getByTestId("alert-mark")).toHaveTextContent("En alerta · 2 días");
    expect(within(row).getByTestId("alert-mark")).toHaveTextContent("Saldo pendiente: $ 20.000,00");
    expect(within(row).getByTestId("overdue-mark")).toHaveTextContent("Fecha esperada vencida");
    // A viewer has no quick action.
    expect(within(row).queryByRole("button", { name: /Registrar cobro/ })).not.toBeInTheDocument();

    const first = new URL(String(calls(spy, "GET", "/settlements/pending-collections")[0][0]), "http://localhost");
    expect(first.searchParams.getAll("state")).toEqual([]);
    expect(first.searchParams.get("alert")).toBeNull();

    await userEvent.type(screen.getByLabelText("Cliente"), "don");
    await userEvent.selectOptions(screen.getByLabelText("Repartidor"), "4");
    await userEvent.selectOptions(screen.getByLabelText("Responsable"), "5");
    await userEvent.selectOptions(screen.getByLabelText("Estado"), "COLLECTED");
    await userEvent.selectOptions(screen.getByLabelText("Antigüedad"), "48");
    await userEvent.click(screen.getByLabelText("Solo en alerta"));
    await waitFor(() => {
      const last = new URL(
        String(calls(spy, "GET", "/settlements/pending-collections").at(-1)![0]),
        "http://localhost",
      );
      expect(Object.fromEntries(last.searchParams)).toEqual({
        state: "COLLECTED",
        customer: "don",
        driver_id: "4",
        responsible_employee_id: "5",
        min_age_hours: "48",
        alert: "true",
        limit: "20",
        offset: "0",
      });
    });
  });

  it("opens filtered by alert from the home link", async () => {
    search.params = new URLSearchParams("alerta=1");
    const spy = mockApi({
      ...base("viewer"),
      "GET /settlements/pending-collections": () => jsonResponse(200, { items: [], total: 0 }),
    });
    renderWithQuery(<PendingList />);
    expect(await screen.findByText("No hay pendientes con esos filtros")).toBeInTheDocument();
    expect(screen.getByLabelText("Solo en alerta")).toBeChecked();
    const first = new URL(String(calls(spy, "GET", "/settlements/pending-collections")[0][0]), "http://localhost");
    expect(first.searchParams.get("alert")).toBe("true");
    search.params = new URLSearchParams();
  });

  it("quick action re-reads the pending and never credits without the confirmation step", async () => {
    const fresh = detail({ balance: "40000.00", collected: "60000.00", state: "PARTIAL" });
    const spy = mockApi({
      ...base("operator", fresh),
      "GET /settlements/pending-collections": () => jsonResponse(200, { items: [item()], total: 1 }),
      "POST /settlements/pending-collections/12/receipts": () => jsonResponse(201, fresh),
    });
    renderWithQuery(<PendingList />);
    await userEvent.click(await screen.findByRole("button", { name: "Registrar cobro de Almacén Don José" }));
    const dialog = await screen.findByRole("dialog", { name: "Registrar cobro" });
    // The list said $100.000; the dialog shows the balance read now.
    expect(await within(dialog).findByTestId("receipt-balance")).toHaveTextContent("$ 40.000,00");
    expect(calls(spy, "GET", "/settlements/pending-collections/12")).toHaveLength(1);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cobrar saldo completo" }));
    await userEvent.selectOptions(within(dialog).getByLabelText("Caja de destino"), "1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar cobro" }));
    const review = within(dialog).getByRole("group", { name: "Revisá el cobro" });
    for (const text of ["Almacén Don José", "$ 40.000,00", "Efectivo", "Caja principal"]) {
      expect(review).toHaveTextContent(text);
    }
    expect(calls(spy, "POST", "/settlements/pending-collections/12/receipts")).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar cobro" }));
    await waitFor(() => expect(calls(spy, "POST", "/settlements/pending-collections/12/receipts")).toHaveLength(1));
    const [body] = bodies(spy, "POST", "/settlements/pending-collections/12/receipts");
    expect(body).toMatchObject({ amount: "40000.00", method: "CASH", destination_account_id: 1 });
    expect(typeof body.client_request_id).toBe("string");
  });
});

describe("receipt form", () => {
  async function openReceipt(role: Role = "operator", extra: Record<string, Handler> = {}) {
    const spy = mockApi({ ...base(role), ...extra });
    renderWithQuery(<PendingDetailView pendingId={12} />);
    await userEvent.click(await screen.findByRole("button", { name: "Registrar cobro" }));
    const dialog = await screen.findByRole("dialog", { name: "Registrar cobro" });
    await within(dialog).findByTestId("receipt-balance");
    return { spy, dialog };
  }

  it("offers only active cash boxes for cash and only active banks for a transfer; no cheque or other", async () => {
    const { dialog } = await openReceipt();
    const options = (label: string) =>
      Array.from(within(dialog).getByLabelText(label).querySelectorAll("option")).map((o) => o.textContent);
    expect(options("Caja de destino")).toEqual(["Elegí…", "Caja principal"]);
    expect(
      within(dialog)
        .getAllByRole("radio")
        .map((r) => r.closest("label")?.textContent),
    ).toEqual(["Efectivo", "Transferencia"]);
    await userEvent.click(within(dialog).getByRole("radio", { name: "Transferencia" }));
    expect(options("Banco de destino")).toEqual(["Elegí…", "Banco"]);
  });

  it("records a transfer for the full balance after confirming", async () => {
    const { spy, dialog } = await openReceipt("operator", {
      "POST /settlements/pending-collections/12/receipts": () => jsonResponse(201, detail({ state: "COLLECTED" })),
    });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cobrar saldo completo" }));
    await userEvent.click(within(dialog).getByRole("radio", { name: "Transferencia" }));
    await userEvent.selectOptions(within(dialog).getByLabelText("Banco de destino"), "3");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar cobro" }));
    expect(within(dialog).getByTestId("receipt-amount")).toHaveTextContent("$ 100.000,00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar cobro" }));
    await waitFor(() =>
      expect(bodies(spy, "POST", "/settlements/pending-collections/12/receipts")[0]).toMatchObject({
        amount: "100000.00",
        method: "TRANSFER",
        destination_account_id: 3,
      }),
    );
  });

  it("refuses more than the balance locally and shows the backend balance when it changed meanwhile", async () => {
    const { dialog } = await openReceipt("operator", {
      "POST /settlements/pending-collections/12/receipts": () =>
        apiError(409, "pending_amount_exceeds_balance", { balance: "30000.00" }),
    });
    await userEvent.type(within(dialog).getByLabelText("Importe"), "150.000");
    await userEvent.selectOptions(within(dialog).getByLabelText("Caja de destino"), "1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar cobro" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("supera el saldo pendiente ($ 100.000,00)");
    await userEvent.clear(within(dialog).getByLabelText("Importe"));
    await userEvent.type(within(dialog).getByLabelText("Importe"), "50.000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar cobro" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar cobro" }));
    // Back to the form, with the current balance in the message.
    expect(await within(dialog).findByRole("button", { name: "Revisar cobro" })).toBeInTheDocument();
    expect(within(dialog).getByRole("alert")).toHaveTextContent("$ 30.000,00");
  });

  it("keeps an uncertain receipt and retries it with the same request id, never a new one", async () => {
    const { spy, dialog } = await openReceipt("operator", {
      "POST /settlements/pending-collections/12/receipts": (_, call) =>
        call === 1 ? apiError(503, "unavailable") : jsonResponse(200, detail()),
    });
    await userEvent.type(within(dialog).getByLabelText("Importe"), "10.000");
    await userEvent.selectOptions(within(dialog).getByLabelText("Caja de destino"), "1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar cobro" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar cobro" }));
    expect(await within(dialog).findByText("Hay una operación sin confirmar")).toBeInTheDocument();
    expect(sessionStorage.length).toBe(1);
    // Closing and reopening the form offers the same attempt.
    const closeButton = within(dialog)
      .getAllByRole("button", { name: "Cerrar" })
      .find((button) => !button.hasAttribute("data-close"))!;
    await userEvent.click(closeButton);
    await userEvent.click(screen.getByRole("button", { name: "Registrar cobro" }));
    const again = await screen.findByRole("dialog", { name: "Registrar cobro" });
    await userEvent.click(await within(again).findByRole("button", { name: "Reintentar la misma operación" }));
    await waitFor(() => expect(sessionStorage.length).toBe(0));
    const sent = bodies(spy, "POST", "/settlements/pending-collections/12/receipts");
    expect(sent).toHaveLength(2);
    expect(sent[1]).toEqual(sent[0]);
  });
});

describe("pending detail", () => {
  it("shows origin, financial state, receipts, resolutions and the history from its events", async () => {
    mockApi(
      base(
        "viewer",
        detail({
          state: "PARTIAL",
          collected: "60000.00",
          resolved: "20000.00",
          balance: "20000.00",
          receipts: [RECEIPT],
          resolutions: [RESOLUTION],
        }),
      ),
    );
    renderWithQuery(<PendingDetailView pendingId={12} />);
    expect(await screen.findByTestId("pending-balance")).toHaveTextContent("$ 20.000,00");
    expect(screen.getByTestId("pending-state")).toHaveTextContent("Parcial");
    expect(screen.getByTestId("amount-in-effect")).toHaveTextContent("$ 100.000,00");
    expect(screen.getByTestId("receipt-31")).toHaveTextContent("Caja principal");
    expect(screen.getByTestId("resolution-41")).toHaveTextContent("Empresa (Incobrables): $ 20.000,00");
    const history = screen.getByRole("list", { name: "Historia del pendiente" });
    expect(history).toHaveTextContent("Pendiente registrado");
    expect(history).toHaveTextContent("Publicado en Cajas por el cierre");
    expect(screen.getByRole("link", { name: "Rendición n.º 1 del 04/10/2026" })).toHaveAttribute(
      "href",
      "/rendiciones/repartos/7",
    );
    // A viewer reads only.
    for (const name of ["Registrar cobro", "Resolver saldo", "Cambiar responsable", "Revertir"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("an operator collects but never resolves, reverses or changes the responsible", async () => {
    mockApi(base("operator", detail({ receipts: [RECEIPT], resolutions: [RESOLUTION] })));
    renderWithQuery(<PendingDetailView pendingId={12} />);
    expect(await screen.findByRole("button", { name: "Registrar cobro" })).toBeInTheDocument();
    for (const name of ["Resolver saldo", "Cambiar responsable", "Revertir"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("an admin resolves only when the parts add up exactly, company and employees", async () => {
    const spy = mockApi({
      ...base("admin", detail({ balance: "40000.00", state: "PARTIAL", collected: "60000.00" })),
      "POST /settlements/pending-collections/12/resolutions": () => jsonResponse(201, detail()),
    });
    renderWithQuery(<PendingDetailView pendingId={12} />);
    await userEvent.click(await screen.findByRole("button", { name: "Resolver saldo" }));
    const dialog = screen.getByRole("dialog", { name: "Resolver saldo" });
    expect(dialog).toHaveTextContent("no se va a cobrar normalmente");
    const confirm = within(dialog).getByRole("button", { name: "Confirmar resolución" });
    expect(within(dialog).getByLabelText("Importe a resolver")).toHaveValue("40000,00");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Cliente cerró");
    await userEvent.type(within(dialog).getByLabelText("Importe, parte 1"), "15.000");
    expect(within(dialog).getByTestId("resolution-assigned")).toHaveTextContent("$ 15.000,00");
    expect(within(dialog).getByTestId("resolution-missing")).toHaveTextContent("$ 25.000,00");
    expect(confirm).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar parte" }));
    await userEvent.selectOptions(within(dialog).getByLabelText("A cargo de, parte 2"), "5");
    await userEvent.type(within(dialog).getByLabelText("Importe, parte 2"), "25.000");
    expect(within(dialog).getByTestId("resolution-missing")).toHaveTextContent("$ 0,00");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() => expect(calls(spy, "POST", "/settlements/pending-collections/12/resolutions")).toHaveLength(1));
    expect(bodies(spy, "POST", "/settlements/pending-collections/12/resolutions")[0]).toMatchObject({
      amount: "40000.00",
      reason: "Cliente cerró",
      allocations: [
        { target: "COMPANY", employee_id: null, amount: "15000.00" },
        { target: "EMPLOYEE", employee_id: 5, amount: "25000.00" },
      ],
    });
  });

  it("an admin reverses with a reason, is warned about the original date, and reads why a reversal is refused", async () => {
    const spy = mockApi({
      ...base("admin", detail({ receipts: [RECEIPT] })),
      "POST /settlements/pending-collections/12/receipts/31/reverse": () => apiError(409, "insufficient_funds"),
    });
    renderWithQuery(<PendingDetailView pendingId={12} />);
    await userEvent.click(
      await within(await screen.findByTestId("receipt-31")).findByRole("button", { name: "Revertir" }),
    );
    const dialog = screen.getByRole("dialog", { name: "Revertir cobro" });
    expect(dialog).toHaveTextContent("Se corregirá la operación financiera en su fecha original");
    expect(dialog).toHaveTextContent("El movimiento original no se borra");
    expect(dialog).not.toHaveTextContent("Eliminar");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Indicá el motivo.");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Mal cargado");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("dejaría inconsistente el saldo histórico");
    const [body] = bodies(spy, "POST", "/settlements/pending-collections/12/receipts/31/reverse");
    expect(body.reason).toBe("Mal cargado");
    expect(typeof body.client_request_id).toBe("string");
  });

  it("an admin reverses a resolution and changes the responsible without reopening the settlement", async () => {
    const spy = mockApi({
      ...base("admin", detail({ resolutions: [RESOLUTION] })),
      "POST /settlements/pending-collections/12/resolutions/41/reverse": () => jsonResponse(201, detail()),
      "PATCH /settlements/pending-collections/12/responsible": () => jsonResponse(200, detail()),
    });
    renderWithQuery(<PendingDetailView pendingId={12} />);
    await userEvent.click(
      await within(await screen.findByTestId("resolution-41")).findByRole("button", { name: "Revertir" }),
    );
    const reverse = screen.getByRole("dialog", { name: "Revertir resolución" });
    await userEvent.type(within(reverse).getByLabelText("Motivo"), "Era otro cliente");
    await userEvent.click(within(reverse).getByRole("button", { name: "Revertir" }));
    await waitFor(() =>
      expect(calls(spy, "POST", "/settlements/pending-collections/12/resolutions/41/reverse")).toHaveLength(1),
    );

    await userEvent.click(screen.getByRole("button", { name: "Cambiar responsable" }));
    const change = screen.getByRole("dialog", { name: "Cambiar responsable" });
    expect(change).toHaveTextContent("Responsable actual: Pedro Gómez");
    expect(change).toHaveTextContent("La rendición no se reabre");
    await userEvent.selectOptions(within(change).getByLabelText("Nuevo responsable"), "5");
    await userEvent.click(within(change).getByRole("button", { name: "Cambiar responsable" }));
    await waitFor(() =>
      expect(bodies(spy, "PATCH", "/settlements/pending-collections/12/responsible")).toEqual([
        { responsible_employee_id: 5, reason: null },
      ]),
    );
  });
});

function settlement(changes: Partial<Settlement> = {}): Settlement {
  const zero = "0.00";
  return {
    id: 7,
    type: "DELIVERY",
    business_date: "2026-10-04",
    employee_id: 4,
    employee_name: "Pedro Gómez",
    sequence_number: 1,
    expected_amount: "100000.00",
    expected_source: "MANUAL",
    external_reference: null,
    notes: null,
    status: "OPEN",
    version: 1,
    created_by: 9,
    created_by_name: "Oscar Operador",
    created_at: "2026-10-04T10:00:00Z",
    updated_at: "2026-10-04T10:00:00Z",
    same_day_settlement_ids: [],
    count_lines: [],
    additions: [],
    transfers: [],
    expenses: [],
    summary: {
      expected: "100000.00",
      cash_counted: zero,
      cash_additions: zero,
      cash_total: zero,
      cash_by_box: [],
      transfers_verified: zero,
      transfers_declared: zero,
      transfers_rejected: zero,
      declared_transfers_count: 0,
      cheques: zero,
      pending: "100000.00",
      expenses: zero,
      justified: "100000.00",
      difference: zero,
      difference_kind: "BALANCED",
    },
    financial_state: "NOT_CLOSED",
    financial_state_detail: "",
    close_in_effect: null,
    closed_by: null,
    closed_by_name: null,
    closed_at: null,
    reopened_by: null,
    reopened_by_name: null,
    reopened_at: null,
    reopen_reason: null,
    current_difference: null,
    pending_collections: [],
    ...changes,
  };
}

const settlementRoutes = (role: Role, current: Settlement, extra: Record<string, Handler> = {}) => ({
  "GET /auth/me": () => jsonResponse(200, me(role)),
  "GET /settlements/accounts": () => jsonResponse(200, ACCOUNTS),
  "GET /settlements/deliveries/7": () => jsonResponse(200, current),
  "GET /settlements/cash-denominations": () => jsonResponse(200, []),
  "GET /settlements/expense-categories": () => jsonResponse(200, []),
  "GET /settlements/employees": () => jsonResponse(200, EMPLOYEES),
  ...extra,
});

describe("pendings inside the settlement", () => {
  it("adds a pending with the driver preselected as responsible and a request id", async () => {
    const spy = mockApi(
      settlementRoutes("operator", settlement(), {
        "POST /settlements/deliveries/7/pending-collections": () => jsonResponse(201, settlement()),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Agregar pendiente" }));
    const dialog = screen.getByRole("dialog", { name: "Agregar pendiente" });
    expect(within(dialog).getByLabelText("Responsable de cobrarlo")).toHaveValue("4");
    await userEvent.type(within(dialog).getByLabelText("Cliente"), "Kiosco Sur");
    await userEvent.type(within(dialog).getByLabelText("Importe"), "25.000");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Paga el lunes");
    await userEvent.type(within(dialog).getByLabelText("Fecha esperada de cobro (opcional)"), "2026-10-06");
    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar pendiente" }));
    await waitFor(() => expect(calls(spy, "POST", "/settlements/deliveries/7/pending-collections")).toHaveLength(1));
    const [body] = bodies(spy, "POST", "/settlements/deliveries/7/pending-collections");
    expect(body).toMatchObject({
      customer_name: "Kiosco Sur",
      amount: "25000.00",
      reason: "Paga el lunes",
      responsible_employee_id: 4,
      expected_date: "2026-10-06",
    });
    expect(typeof body.client_request_id).toBe("string");
  });

  it("corrects and voids a draft pending; a viewer only reads", async () => {
    const draft = pending({ state: "DRAFT", amount_in_effect: null, customer_in_effect: null, balance: null });
    const spy = mockApi(
      settlementRoutes("operator", settlement({ pending_collections: [draft] }), {
        "PATCH /settlements/deliveries/7/pending-collections/12": () => jsonResponse(200, settlement()),
        "POST /settlements/deliveries/7/pending-collections/12/void": () => jsonResponse(200, settlement()),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    const row = await screen.findByTestId("pending-row-12");
    expect(row).toHaveTextContent("En rendición abierta");
    await userEvent.click(within(row).getByRole("button", { name: "Corregir" }));
    const edit = screen.getByRole("dialog", { name: "Corregir pendiente" });
    await userEvent.clear(within(edit).getByLabelText("Importe"));
    await userEvent.type(within(edit).getByLabelText("Importe"), "80.000");
    await userEvent.click(within(edit).getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(bodies(spy, "PATCH", "/settlements/deliveries/7/pending-collections/12")[0]).toMatchObject({
        amount: "80000.00",
        customer_name: "Almacén Don José",
      }),
    );
    await userEvent.click(within(screen.getByTestId("pending-row-12")).getByRole("button", { name: "Anular" }));
    const voiding = screen.getByRole("dialog", { name: "Anular pendiente" });
    await userEvent.type(within(voiding).getByLabelText("Motivo"), "Pagó en el momento");
    await userEvent.click(within(voiding).getByRole("button", { name: "Anular" }));
    await waitFor(() =>
      expect(bodies(spy, "POST", "/settlements/deliveries/7/pending-collections/12/void")).toEqual([
        { reason: "Pagó en el momento" },
      ]),
    );
  });

  it("explains a correction below what was applied", async () => {
    mockApi(
      settlementRoutes("operator", settlement({ pending_collections: [pending()] }), {
        "PATCH /settlements/deliveries/7/pending-collections/12": () =>
          apiError(409, "pending_below_applied", { applied: "60000.00" }),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    await userEvent.click(
      await within(await screen.findByTestId("pending-row-12")).findByRole("button", { name: "Corregir" }),
    );
    const edit = screen.getByRole("dialog", { name: "Corregir pendiente" });
    await userEvent.clear(within(edit).getByLabelText("Importe"));
    await userEvent.type(within(edit).getByLabelText("Importe"), "50.000");
    await userEvent.click(within(edit).getByRole("button", { name: "Guardar" }));
    expect(await within(edit).findByRole("alert")).toHaveTextContent(
      "El pendiente ya tiene $ 60.000,00 cobrados o resueltos: su importe no puede quedar por debajo.",
    );
  });

  it("during a correction keeps what is in effect apart from what the new close will apply", async () => {
    const modified = pending({
      amount: "70000.00",
      load_change: "MODIFIED",
      load_change_detail: "Modificación pendiente de confirmar en la rendición.",
    });
    const removed = pending({
      id: 13,
      customer_name: "Bar Centro",
      customer_in_effect: "Bar Centro",
      voided: true,
      void_reason: "Pagó",
      load_change: "MARKED_FOR_REMOVAL",
    });
    mockApi(
      settlementRoutes(
        "viewer",
        settlement({ financial_state: "BEING_CORRECTED", version: 2, pending_collections: [modified, removed] }),
      ),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    const first = await screen.findByTestId("pending-row-12");
    expect(within(first).getByTestId("load-change")).toHaveTextContent("Vigente: $ 100.000,00");
    expect(within(first).getByTestId("load-change")).toHaveTextContent("Nuevo importe propuesto: $ 70.000,00");
    expect(first).toHaveTextContent("Pendiente");
    const second = screen.getByTestId("pending-row-13");
    expect(within(second).getByTestId("load-change")).toHaveTextContent("Marcado para quitar en esta corrección");
    expect(within(second).getByTestId("load-change")).toHaveTextContent(
      "Sigue vigente por $ 100.000,00 hasta confirmar el nuevo cierre.",
    );
    // Not financially voided before the new close.
    expect(second).not.toHaveTextContent("Anulado");
    expect(screen.queryByRole("button", { name: "Agregar pendiente" })).not.toBeInTheDocument();
  });
});

describe("home, menu, configuration and Cajas", () => {
  it("shows the alert count and balance with a link to the filtered list, and a discreet menu badge", async () => {
    mockApi({
      "GET /settlements/pending-collections/summary": () =>
        jsonResponse(200, { alert_hours: 24, count: 4, balance: "325000.00" }),
    });
    renderWithQuery(
      <>
        <PendingAlertCard />
        <PendingAlertBadge />
      </>,
    );
    expect(await screen.findByTestId("alert-count")).toHaveTextContent("4 pendientes en alerta");
    expect(screen.getByTestId("alert-balance")).toHaveTextContent("$ 325.000,00 pendientes");
    expect(screen.getByRole("link", { name: "Ver pendientes" })).toHaveAttribute(
      "href",
      "/rendiciones/pendientes?alerta=1",
    );
    expect(screen.getByTestId("pending-alert-badge")).toHaveTextContent("4");
    expect(screen.getByLabelText("4 pendientes en alerta")).toBeInTheDocument();
  });

  it("shows no badge when nothing is in alert", async () => {
    mockApi({
      "GET /settlements/pending-collections/summary": () =>
        jsonResponse(200, { alert_hours: 24, count: 0, balance: "0.00" }),
    });
    renderWithQuery(<PendingAlertCard />);
    expect(await screen.findByText("No hay pendientes en alerta.")).toBeInTheDocument();
    expect(screen.queryByTestId("pending-alert-badge")).not.toBeInTheDocument();
  });

  it("an admin edits the alert hours (24 by default) within the backend range", async () => {
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("admin")),
      "GET /settlements/pending-alert-settings": () =>
        jsonResponse(200, { alert_hours: 24, is_default: true, min_hours: 1, max_hours: 720 }),
      "PUT /settlements/pending-alert-settings": () =>
        jsonResponse(200, { alert_hours: 48, is_default: false, min_hours: 1, max_hours: 720 }),
    });
    renderWithQuery(<PendingAlertSettings />);
    const field = await screen.findByLabelText("Alertar pendientes después de");
    expect(field).toHaveValue(24);
    expect(
      screen.getByText("Un pendiente con saldo aparecerá en alerta cuando supere este tiempo desde su creación."),
    ).toBeInTheDocument();
    await userEvent.clear(field);
    await userEvent.type(field, "1000");
    await userEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(screen.getByRole("alert")).toHaveTextContent("de 1 a 720 horas");
    expect(calls(spy, "PUT", "/settlements/pending-alert-settings")).toHaveLength(0);
    await userEvent.clear(field);
    await userEvent.type(field, "48");
    await userEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Guardado");
    expect(bodies(spy, "PUT", "/settlements/pending-alert-settings")).toEqual([{ alert_hours: 48 }]);
  });

  it("a non admin reads the alert hours without editing them", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("operator")),
      "GET /settlements/pending-alert-settings": () =>
        jsonResponse(200, { alert_hours: 24, is_default: true, min_hours: 1, max_hours: 720 }),
    });
    renderWithQuery(<PendingAlertSettings />);
    expect(await screen.findByText("24 horas")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Guardar" })).not.toBeInTheDocument();
  });

  it("links a Cajas operation of a pending to its detail", () => {
    renderWithQuery(
      <>
        <span>
          <SourceLink sourceType="pending_collection" sourceId={12} />
        </span>
        <span>
          <SourceLink sourceType="settlement" sourceId={7} />
        </span>
        <span>
          <SourceLink sourceType={null} sourceId={null} />
        </span>
      </>,
    );
    expect(screen.getByRole("link", { name: "Pendiente de cobro #12" })).toHaveAttribute(
      "href",
      "/rendiciones/pendientes/12",
    );
    expect(screen.getByText("Rendición #7")).not.toHaveAttribute("href");
    expect(screen.getByText("Carga manual")).toBeInTheDocument();
  });
});
