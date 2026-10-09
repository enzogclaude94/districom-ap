import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Account } from "@/modules/settlements/cash/api";
import type {
  ClosingPreview,
  Denomination,
  History,
  Settlement,
  SettlementItem,
  Summary,
  Transfer,
} from "@/modules/settlements/delivery/api";
import { DeliveryList } from "@/modules/settlements/delivery/delivery-list";
import { deliveryErrorView, STALE_MESSAGE } from "@/modules/settlements/delivery/errors";
import { timeline } from "@/modules/settlements/delivery/history-tab";
import { differenceText } from "@/modules/settlements/delivery/labels";
import { fromCents, toCents } from "@/modules/settlements/delivery/money";
import { BEING_CORRECTED_BANNER, SettlementDetail } from "@/modules/settlements/delivery/settlement-detail";
import { ApiError } from "@/lib/api/client";

import { jsonResponse, renderWithQuery } from "./render";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: vi.fn() }) }));

type Role = "viewer" | "operator" | "admin";
type Handler = (init: RequestInit | undefined, call: number) => Response | Promise<Response>;

const me = (role: Role) => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "settlements", name: "Rendiciones", role }],
});

const ACCOUNTS: Account[] = [
  {
    id: 1,
    code: "main_cash",
    name: "Caja principal",
    kind: "CASH_BOX",
    allow_negative: false,
    active: true,
    balance: "0.00",
    overdrawn: false,
    overdrawn_label: null,
  },
  {
    id: 2,
    code: "change_cash",
    name: "Caja de cambio",
    kind: "CASH_BOX",
    allow_negative: false,
    active: true,
    balance: "0.00",
    overdrawn: false,
    overdrawn_label: null,
  },
  {
    id: 3,
    code: "bank",
    name: "Banco",
    kind: "BANK",
    allow_negative: false,
    active: true,
    balance: "0.00",
    overdrawn: false,
    overdrawn_label: null,
  },
];

const DENOMINATIONS: Denomination[] = [
  {
    code: "ARS_1000",
    value: "1000.00",
    active: true,
    suggested_destination: "change_cash",
    suggested_destination_account_id: 2,
    suggested_destination_name: "Caja de cambio",
  },
  {
    code: "ARS_20000",
    value: "20000.00",
    active: true,
    suggested_destination: "main_cash",
    suggested_destination_account_id: 1,
    suggested_destination_name: "Caja principal",
  },
  {
    code: "ARS_50",
    value: "50.00",
    active: false,
    suggested_destination: null,
    suggested_destination_account_id: null,
    suggested_destination_name: null,
  },
];

function summary(changes: Partial<Summary> = {}): Summary {
  return {
    expected: "100000.00",
    cash_counted: "99000.00",
    cash_additions: "0.00",
    cash_total: "99000.00",
    cash_by_box: [
      { account_id: 1, account_name: "Caja principal", counted: "99000.00", additions: "0.00", total: "99000.00" },
    ],
    transfers_verified: "0.00",
    transfers_declared: "0.00",
    transfers_rejected: "0.00",
    declared_transfers_count: 0,
    cheques: "0.00",
    pending: "0.00",
    expenses: "0.00",
    justified: "99000.00",
    difference: "-1000.00",
    difference_kind: "SHORTAGE",
    ...changes,
  };
}

function settlement(changes: Partial<Settlement> = {}): Settlement {
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
    cheques: [],
    expenses: [],
    summary: summary(),
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

function preview(changes: Partial<ClosingPreview> = {}): ClosingPreview {
  return {
    settlement_id: 7,
    version: 1,
    status: "OPEN",
    financial_state: "NOT_CLOSED",
    summary: summary(),
    difference: "-1000.00",
    difference_kind: "SHORTAGE",
    amount_to_allocate: "1000.00",
    blockers: [],
    legs: [],
    preview_token: "a".repeat(64),
    close_in_effect: null,
    pending_collections: [],
    ...changes,
  };
}

const BALANCED = preview({
  summary: summary({ justified: "100000.00", difference: "0.00", difference_kind: "BALANCED" }),
  difference: "0.00",
  difference_kind: "BALANCED",
  amount_to_allocate: "0.00",
});

const TRANSFER: Transfer = {
  id: 3,
  amount: "30000.00",
  bank_account_id: 3,
  bank_account_name: "Banco",
  reference: "OP-55",
  payer_name: "Almacén Don José",
  status: "DECLARED",
  status_reason: null,
  status_changed_by: 9,
  status_changed_by_name: "Oscar Operador",
  status_changed_at: "2026-10-04T11:00:00Z",
  created_by: 9,
  created_at: "2026-10-04T11:00:00Z",
  events: [],
};

function mockApi(routes: Record<string, Handler>) {
  const counts = new Map<string, number>();
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    const handler = routes[key];
    if (!handler) return jsonResponse(404, { code: "not_found", message: key, details: null, request_id: null });
    const call = (counts.get(key) ?? 0) + 1;
    counts.set(key, call);
    return handler(init, call);
  });
}

function detailRoutes(role: Role, current: Settlement, extra: Record<string, Handler> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, me(role)),
    "GET /settlements/accounts": () => jsonResponse(200, ACCOUNTS),
    "GET /settlements/deliveries/7": () => jsonResponse(200, current),
    "GET /settlements/cash-denominations": () => jsonResponse(200, DENOMINATIONS),
    "GET /settlements/expense-categories": () => jsonResponse(200, [{ id: 1, name: "Peajes", active: true }]),
    "GET /settlements/employees": () =>
      jsonResponse(200, [
        { id: 5, full_name: "Juan Ayudante" },
        { id: 4, full_name: "Pedro Gómez" },
      ]),
    ...extra,
  };
}

const calls = (spy: ReturnType<typeof mockApi>, method: string, path: string) =>
  spy.mock.calls.filter(
    ([input, init]) => String(input).startsWith(`/api/v1${path}`) && (init?.method ?? "GET") === method,
  );

const bodies = (spy: ReturnType<typeof mockApi>, path: string) =>
  calls(spy, "POST", path).map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

const apiError = (status: number, code: string) =>
  jsonResponse(status, { code, message: "Mensaje del backend.", details: null, request_id: "req-1" });

async function openClose() {
  await userEvent.click(await screen.findByRole("button", { name: "Cerrar rendición" }));
  return screen.findByRole("dialog");
}

describe("money helpers and labels", () => {
  it("adds cents exactly and formats differences in words", () => {
    expect(toCents("0.10") + toCents("0.20")).toBe(30);
    expect(fromCents(toCents("999999999999.99") + 1)).toBe("1000000000000.00");
    expect(fromCents(-150)).toBe("-1.50");
    expect(differenceText("-500.00")).toBe("Faltante $ 500,00");
    expect(differenceText("200.00")).toBe("Sobrante $ 200,00");
    expect(differenceText("0.00")).toBe("Balanceada");
  });

  it("maps stale, uncertain and business errors", () => {
    expect(
      deliveryErrorView(
        new ApiError(409, { code: "settlement_preview_stale", message: "x", details: null, request_id: null }),
      ),
    ).toEqual({
      message: STALE_MESSAGE,
      stale: true,
    });
    expect(
      deliveryErrorView(new ApiError(503, { code: "x", message: "x", details: null, request_id: null })).uncertain,
    ).toBe(true);
    expect(deliveryErrorView(new TypeError("fetch failed")).uncertain).toBe(true);
    const business = new ApiError(409, {
      code: "settlement_closed",
      message: "La rendición está cerrada.",
      details: null,
      request_id: null,
    });
    expect(deliveryErrorView(business).message).toBe("La rendición está cerrada.");
  });
});

describe("delivery list", () => {
  const ITEM: SettlementItem = {
    id: 7,
    type: "DELIVERY",
    business_date: "2026-10-04",
    employee_id: 4,
    employee_name: "Pedro Gómez",
    sequence_number: 2,
    expected_amount: "100000.00",
    external_reference: "HR-12",
    status: "OPEN",
    version: 2,
    financial_state: "BEING_CORRECTED",
    created_at: "2026-10-04T10:00:00Z",
    justified: "99000.00",
    difference: "-1000.00",
  };

  it("lists settlements with state and difference and filters by financial state", async () => {
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("operator")),
      "GET /settlements/employees": () => jsonResponse(200, [{ id: 4, full_name: "Pedro Gómez" }]),
      "GET /settlements/deliveries": () => jsonResponse(200, { items: [ITEM], total: 1 }),
    });
    renderWithQuery(<DeliveryList />);
    const row = (await screen.findByText("Rendición n.º 2")).closest("tr")!;
    expect(within(row).getByText("En corrección")).toBeInTheDocument();
    expect(within(row).getByText("Faltante $ 1.000,00")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: /Abrir Rendición n.º 2 de Pedro Gómez/ })).toHaveAttribute(
      "href",
      "/rendiciones/repartos/7",
    );
    expect(screen.getByRole("button", { name: "Nueva rendición" })).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Estado"), "CLOSED");
    await waitFor(() =>
      expect(
        calls(spy, "GET", "/settlements/deliveries").some(([input]) =>
          String(input).includes("financial_state=CLOSED"),
        ),
      ).toBe(true),
    );
  });

  it("hides Nueva rendición from viewers and explains an empty list", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("viewer")),
      "GET /settlements/employees": () => jsonResponse(200, []),
      "GET /settlements/deliveries": () => jsonResponse(200, { items: [], total: 0 }),
    });
    renderWithQuery(<DeliveryList />);
    expect(await screen.findByText("Todavía no hay rendiciones")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nueva rendición" })).not.toBeInTheDocument();
  });

  it("warns about another settlement the same day and needs an explicit yes; the number is never typed", async () => {
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("operator")),
      "GET /settlements/employees": () => jsonResponse(200, []),
      "GET /settlements/drivers": () => jsonResponse(200, [{ id: 4, full_name: "Pedro Gómez" }]),
      "GET /settlements/deliveries": (_, call) =>
        jsonResponse(
          200,
          call === 1 ? { items: [], total: 0 } : { items: [{ ...ITEM, sequence_number: 1 }], total: 1 },
        ),
      "POST /settlements/deliveries": () => jsonResponse(201, settlement({ id: 8, sequence_number: 2 })),
    });
    renderWithQuery(<DeliveryList />);
    await userEvent.click(await screen.findByRole("button", { name: "Nueva rendición" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByLabelText(/número/i)).not.toBeInTheDocument();
    await userEvent.selectOptions(await within(dialog).findByLabelText("Repartidor"), "4");
    expect(await within(dialog).findByTestId("same-day-warning")).toHaveTextContent(
      "Pedro Gómez ya tiene una rendición",
    );
    await userEvent.type(within(dialog).getByLabelText("Importe esperado"), "50.000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear rendición" }));
    expect(await within(dialog).findByText("Confirmá que es otra rendición del mismo día.")).toBeInTheDocument();
    expect(bodies(spy, "/settlements/deliveries")).toHaveLength(0);

    await userEvent.click(within(dialog).getByLabelText("Sí, es otra rendición del mismo día"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear rendición" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/rendiciones/repartos/8"));
    const [body] = bodies(spy, "/settlements/deliveries");
    expect(body).toMatchObject({ employee_id: 4, expected_amount: "50000.00" });
    expect(body).not.toHaveProperty("sequence_number");
  });

  it("keeps the request id of a creation whose outcome is unknown and retries it as is", async () => {
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("operator")),
      "GET /settlements/employees": () => jsonResponse(200, []),
      "GET /settlements/drivers": () => jsonResponse(200, [{ id: 4, full_name: "Pedro Gómez" }]),
      "GET /settlements/deliveries": () => jsonResponse(200, { items: [], total: 0 }),
      "POST /settlements/deliveries": (_, call) =>
        call === 1 ? apiError(502, "http_error") : jsonResponse(200, settlement({ id: 8 })),
    });
    renderWithQuery(<DeliveryList />);
    await userEvent.click(await screen.findByRole("button", { name: "Nueva rendición" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.selectOptions(await within(dialog).findByLabelText("Repartidor"), "4");
    await userEvent.type(within(dialog).getByLabelText("Importe esperado"), "1000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear rendición" }));
    expect(await within(dialog).findByText("Hay una operación sin confirmar")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Reintentar la misma operación" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/rendiciones/repartos/8"));
    const [first, second] = bodies(spy, "/settlements/deliveries");
    expect(second).toEqual(first);
  });
});

describe("settlement board and load", () => {
  it("shows the backend totals, flags a declared transfer as blocking and offers the operator's actions", async () => {
    mockApi(
      detailRoutes(
        "operator",
        settlement({
          transfers: [TRANSFER],
          summary: summary({ transfers_declared: "30000.00", declared_transfers_count: 1 }),
        }),
      ),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    expect(await screen.findByRole("heading", { name: "Rendición n.º 1 · Pedro Gómez" })).toBeInTheDocument();
    expect(screen.getByTestId("board-difference")).toHaveTextContent("Faltante $ 1.000,00");
    expect(screen.getByText(/1 declarada por \$ 30\.000,00: bloquea el cierre/)).toBeInTheDocument();
    const row = screen.getByText("Almacén Don José").closest("tr")!;
    expect(within(row).getByText("Declarada")).toBeInTheDocument();
    expect(within(row).getByText("Bloquea el cierre")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Verificar" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Corregir decisión" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reabrir para corregir" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Editar agregado/)).not.toBeInTheDocument();
  });

  it("is read only for a viewer", async () => {
    mockApi(detailRoutes("viewer", settlement({ transfers: [TRANSFER] })));
    renderWithQuery(<SettlementDetail settlementId={7} />);
    await screen.findByText("Almacén Don José");
    for (const name of ["Cerrar rendición", "Verificar", "Agregar efectivo", "Cargar conteo", "Registrar gasto"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("counts active banknotes only, with the suggested box preselected and a visual subtotal", async () => {
    const spy = mockApi(
      detailRoutes("operator", settlement(), {
        "PUT /settlements/deliveries/7/count": () => jsonResponse(200, settlement()),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Cargar conteo" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByLabelText(/\$ 50,00/)).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Caja de los billetes de $ 20.000,00")).toHaveValue("1");
    expect(within(dialog).getByLabelText("Caja de los billetes de $ 1.000,00")).toHaveValue("2");
    await userEvent.type(within(dialog).getByLabelText("Cantidad de billetes de $ 20.000,00"), "4");
    await userEvent.type(within(dialog).getByLabelText("Cantidad de billetes de $ 1.000,00"), "19");
    expect(within(dialog).getByTestId("count-total")).toHaveTextContent("$ 99.000,00");
    await userEvent.selectOptions(within(dialog).getByLabelText("Caja de los billetes de $ 1.000,00"), "1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar conteo" }));
    await waitFor(() => expect(calls(spy, "PUT", "/settlements/deliveries/7/count")).toHaveLength(1));
    const [[, init]] = calls(spy, "PUT", "/settlements/deliveries/7/count");
    expect(JSON.parse(String(init?.body))).toEqual({
      lines: [
        { denomination_code: "ARS_20000", quantity: 4, destination_account_id: 1 },
        { denomination_code: "ARS_1000", quantity: 19, destination_account_id: 1 },
      ],
    });
  });

  it("voids an addition with a reason and keeps voided expenses visible", async () => {
    const addition = {
      id: 11,
      amount: "500.00",
      destination_account_id: 1,
      destination_account_name: "Caja principal",
      description: "Monedas",
      created_by: 9,
      created_by_name: "Oscar Operador",
      created_at: "2026-10-04T12:00:00Z",
      voided: false,
      voided_by: null,
      voided_by_name: null,
      voided_at: null,
      void_reason: null,
    };
    const expense = {
      id: 12,
      category_id: 1,
      category_name: "Peajes",
      amount: "2000.00",
      description: null,
      created_by: 9,
      created_by_name: "Oscar Operador",
      created_at: "2026-10-04T12:00:00Z",
      voided: true,
      voided_by: 9,
      voided_by_name: "Oscar Operador",
      voided_at: "2026-10-04T13:00:00Z",
      void_reason: "Cargado dos veces",
    };
    const spy = mockApi(
      detailRoutes("operator", settlement({ additions: [addition], expenses: [expense] }), {
        "POST /settlements/deliveries/7/additions/11/void": () => jsonResponse(200, settlement()),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    const expenseRow = (await screen.findByText("Peajes")).closest("tr")!;
    expect(within(expenseRow).getByText("Anulado")).toBeInTheDocument();
    expect(within(expenseRow).getByText("Motivo: Cargado dos veces")).toBeInTheDocument();
    const additionRow = screen.getByText("Monedas").closest("tr")!;
    await userEvent.click(within(additionRow).getByRole("button", { name: "Anular" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Anular" }));
    expect(await within(dialog).findByText("Indicá el motivo.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Era de otra rendición");
    await userEvent.click(within(dialog).getByRole("button", { name: "Anular" }));
    await waitFor(() =>
      expect(bodies(spy, "/settlements/deliveries/7/additions/11/void")).toEqual([{ reason: "Era de otra rendición" }]),
    );
  });
});

describe("close flow", () => {
  it("balanced: shows Diferencia $0 and still needs Confirmar cierre, sending the preview token", async () => {
    const spy = mockApi(
      detailRoutes("operator", settlement(), {
        "GET /settlements/deliveries/7/closing-preview": () => jsonResponse(200, BALANCED),
        "POST /settlements/deliveries/7/close": () =>
          jsonResponse(
            201,
            settlement({
              status: "CLOSED",
              financial_state: "CLOSED",
              close_in_effect: { operation_id: 40, settlement_version: 1, closed_at: "2026-10-04T15:00:00Z" },
            }),
          ),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    const dialog = await openClose();
    expect(await within(dialog).findByTestId("balanced")).toHaveTextContent("Rendición balanceada — Diferencia $0");
    expect(bodies(spy, "/settlements/deliveries/7/close")).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar cierre" }));
    await waitFor(() =>
      expect(bodies(spy, "/settlements/deliveries/7/close")).toEqual([
        { version: 1, preview_token: "a".repeat(64), allocations: [], confirm: true },
      ]),
    );
    expect(await screen.findByRole("link", { name: "Ver el cierre en Cajas" })).toHaveAttribute(
      "href",
      "/rendiciones/cajas/operaciones/40",
    );
  });

  it("shortage: blocks the confirmation until the allocation adds up exactly, across several employees", async () => {
    const spy = mockApi(
      detailRoutes("operator", settlement(), {
        "GET /settlements/deliveries/7/closing-preview": () => jsonResponse(200, preview()),
        "POST /settlements/deliveries/7/close": () =>
          jsonResponse(201, settlement({ status: "CLOSED", financial_state: "CLOSED" })),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    const dialog = await openClose();
    const confirm = await within(dialog).findByRole("button", { name: "Confirmar cierre" });
    expect(confirm).toBeDisabled();
    expect(within(dialog).queryByRole("option", { name: "La empresa" })).not.toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Importe de la parte 1"), "600");
    expect(within(dialog).getByTestId("assigned")).toHaveTextContent("$ 600,00");
    expect(within(dialog).getByTestId("remaining")).toHaveTextContent("$ 400,00");
    expect(confirm).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar otra parte" }));
    await userEvent.selectOptions(within(dialog).getByLabelText("Parte 2: quién"), "5");
    await userEvent.type(within(dialog).getByLabelText("Importe de la parte 2"), "500");
    expect(within(dialog).getByText("Asignado de más")).toBeInTheDocument();
    expect(confirm).toBeDisabled();
    await userEvent.clear(within(dialog).getByLabelText("Importe de la parte 2"));
    await userEvent.type(within(dialog).getByLabelText("Importe de la parte 2"), "400");
    expect(within(dialog).getByTestId("remaining")).toHaveTextContent("$ 0,00");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() => expect(bodies(spy, "/settlements/deliveries/7/close")).toHaveLength(1));
    expect(bodies(spy, "/settlements/deliveries/7/close")[0].allocations).toEqual([
      { target: "EMPLOYEE", employee_id: 4, amount: "600.00", reason: null },
      { target: "EMPLOYEE", employee_id: 5, amount: "400.00", reason: null },
    ]);
  });

  it("an admin can give a part to the company, only with a reason", async () => {
    mockApi(
      detailRoutes("admin", settlement(), {
        "GET /settlements/deliveries/7/closing-preview": () => jsonResponse(200, preview()),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    const dialog = await openClose();
    await userEvent.selectOptions(await within(dialog).findByLabelText("Parte 1: quién"), "company");
    await userEvent.type(within(dialog).getByLabelText("Importe de la parte 1"), "1000");
    expect(within(dialog).getByText("Indicá el motivo por el que la empresa absorbe su parte.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Confirmar cierre" })).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Motivo por el que la empresa la absorbe"), "Billete falso");
    expect(within(dialog).getByRole("button", { name: "Confirmar cierre" })).toBeEnabled();
  });

  it("stale preview: never retried automatically, a new preview is loaded and confirmed with its new token", async () => {
    const newToken = "b".repeat(64);
    const spy = mockApi(
      detailRoutes("operator", settlement(), {
        "GET /settlements/deliveries/7/closing-preview": (_, call) =>
          jsonResponse(200, call === 1 ? BALANCED : { ...BALANCED, preview_token: newToken }),
        "POST /settlements/deliveries/7/close": (_, call) =>
          call === 1
            ? apiError(409, "settlement_preview_stale")
            : jsonResponse(201, settlement({ status: "CLOSED", financial_state: "CLOSED" })),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    const dialog = await openClose();
    await userEvent.click(await within(dialog).findByRole("button", { name: "Confirmar cierre" }));
    expect(await within(dialog).findByText(STALE_MESSAGE)).toBeInTheDocument();
    await waitFor(() => expect(calls(spy, "GET", "/settlements/deliveries/7/closing-preview")).toHaveLength(2));
    // Give any automatic retry the chance to happen: there must be none.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(bodies(spy, "/settlements/deliveries/7/close")).toHaveLength(1);

    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Confirmar cierre" })).toBeEnabled());
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar cierre" }));
    await waitFor(() => expect(bodies(spy, "/settlements/deliveries/7/close")).toHaveLength(2));
    const [first, second] = bodies(spy, "/settlements/deliveries/7/close");
    expect(first.preview_token).toBe("a".repeat(64));
    expect(second.preview_token).toBe(newToken);
  });

  it("an uncertain close is kept and retried with exactly the same body", async () => {
    const spy = mockApi(
      detailRoutes("operator", settlement(), {
        "GET /settlements/deliveries/7/closing-preview": () => jsonResponse(200, BALANCED),
        "POST /settlements/deliveries/7/close": (_, call) =>
          call === 1
            ? apiError(504, "http_error")
            : jsonResponse(200, settlement({ status: "CLOSED", financial_state: "CLOSED" })),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    let dialog = await openClose();
    await userEvent.click(await within(dialog).findByRole("button", { name: "Confirmar cierre" }));
    expect(await within(dialog).findByText("Hay una operación sin confirmar")).toBeInTheDocument();
    // Closing and reopening the dialog keeps the attempt: it is never replaced by a new close.
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    dialog = await openClose();
    await userEvent.click(within(dialog).getByRole("button", { name: "Reintentar la misma operación" }));
    await waitFor(() => expect(bodies(spy, "/settlements/deliveries/7/close")).toHaveLength(2));
    const [first, second] = bodies(spy, "/settlements/deliveries/7/close");
    expect(second).toEqual(first);
  });

  it("shows the blockers of the preview and never enables the confirmation", async () => {
    mockApi(
      detailRoutes("operator", settlement(), {
        "GET /settlements/deliveries/7/closing-preview": () =>
          jsonResponse(200, {
            ...BALANCED,
            blockers: [{ code: "declared_transfers_pending", message: "Hay 1 transferencia declarada sin verificar." }],
          }),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    const dialog = await openClose();
    expect(await within(dialog).findByText("Hay 1 transferencia declarada sin verificar.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Confirmar cierre" })).toBeDisabled();
  });
});

describe("reopen and history", () => {
  const closedAt = "2026-10-04T15:00:00Z";
  const CLOSED = settlement({
    status: "CLOSED",
    financial_state: "CLOSED",
    close_in_effect: { operation_id: 40, settlement_version: 1, closed_at: closedAt },
    closed_by: 9,
    closed_by_name: "Oscar Operador",
    closed_at: closedAt,
  });

  it("only an admin reopens, with a reason; the banner says the close stays in Cajas", async () => {
    const reopened = settlement({
      version: 2,
      financial_state: "BEING_CORRECTED",
      close_in_effect: CLOSED.close_in_effect,
      reopened_by: 1,
      reopened_by_name: "Ana Admin",
      reopened_at: "2026-10-04T16:00:00Z",
      reopen_reason: "Faltaba un billete",
    });
    const spy = mockApi(
      detailRoutes("admin", CLOSED, {
        "POST /settlements/deliveries/7/reopen": () => jsonResponse(200, reopened),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Reabrir para corregir" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reabrir" }));
    expect(await within(dialog).findByText("Indicá el motivo de la reapertura.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Faltaba un billete");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reabrir" }));
    await waitFor(() =>
      expect(bodies(spy, "/settlements/deliveries/7/reopen")).toEqual([{ version: 1, reason: "Faltaba un billete" }]),
    );
  });

  it("an operator cannot reopen", async () => {
    mockApi(detailRoutes("operator", CLOSED));
    renderWithQuery(<SettlementDetail settlementId={7} />);
    expect(await screen.findByText(/Cerrada por Oscar Operador/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reabrir para corregir" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cerrar rendición" })).not.toBeInTheDocument();
  });

  it("shows the being-corrected banner", async () => {
    mockApi(
      detailRoutes(
        "operator",
        settlement({ version: 2, financial_state: "BEING_CORRECTED", close_in_effect: CLOSED.close_in_effect }),
      ),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    expect(await screen.findByText(BEING_CORRECTED_BANNER)).toBeInTheDocument();
    expect(BEING_CORRECTED_BANNER).toBe(
      "Esta rendición está siendo corregida. El cierre anterior continúa vigente en Cajas hasta que confirmes el nuevo cierre.",
    );
  });

  it("orders the history: creation, close, difference, reopening, reversal, new close", () => {
    const leg = {
      account_id: 1,
      account_name: "Caja principal",
      account_kind: "CASH_BOX",
      employee_id: null,
      amount: "99000.00",
    };
    const history: History = {
      settlement_id: 7,
      version: 2,
      status: "CLOSED",
      financial_state: "CLOSED",
      closings: [
        {
          settlement_version: 1,
          operation_id: 40,
          reversal_operation_id: null,
          reversed_by_operation_id: 41,
          in_effect: false,
          closed_by: 9,
          closed_by_name: "Oscar",
          closed_at: closedAt,
          business_date: "2026-10-04",
          legs: [leg],
        },
        {
          settlement_version: 2,
          operation_id: 42,
          reversal_operation_id: 41,
          reversed_by_operation_id: null,
          in_effect: true,
          closed_by: 9,
          closed_by_name: "Oscar",
          closed_at: "2026-10-04T17:00:00Z",
          business_date: "2026-10-04",
          legs: [leg],
        },
      ],
      reopenings: [
        {
          from_version: 1,
          to_version: 2,
          reason: "Faltaba un billete",
          reopened_by: 1,
          reopened_by_name: "Ana",
          reopened_at: "2026-10-04T16:00:00Z",
          close_operation_id: 40,
        },
      ],
      differences: [
        {
          id: 1,
          settlement_version: 1,
          kind: "SHORTAGE",
          amount: "1000.00",
          close_operation_id: 40,
          created_by: 9,
          created_by_name: "Oscar",
          created_at: "2026-10-04T14:59:59.900Z", // written just before its close row
          superseded_at: "2026-10-04T17:00:00Z",
          superseded_by: 9,
          superseded_by_name: "Oscar",
          allocations: [],
        },
      ],
    };
    expect(timeline(settlement(), history).map((event) => event.title)).toEqual([
      "Rendición creada",
      "Cierre",
      "Faltante de $ 1.000,00 (versión 1)",
      "Reapertura (versión 1 → 2)",
      "Reverso del cierre de la versión 1",
      "Nuevo cierre (versión 2)",
    ]);
  });
});
