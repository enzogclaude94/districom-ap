import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import { todayIso } from "@/lib/format";
import type { Account, Operation } from "@/modules/settlements/cash/api";
import { CashBoard } from "@/modules/settlements/cash/cash-board";
import { OperationDetail } from "@/modules/settlements/cash/operation-detail";
import type { ExpenseDetail } from "@/modules/settlements/expenses/api";
import { expenseErrorView } from "@/modules/settlements/expenses/errors";
import { ExpenseDetailView } from "@/modules/settlements/expenses/expense-detail";
import { ExpenseList } from "@/modules/settlements/expenses/expense-list";
import { fuelSummary } from "@/modules/settlements/expenses/labels";

import { jsonResponse, renderWithQuery } from "./render";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

const account = (id: number, name: string, kind: Account["kind"], changes: Partial<Account> = {}): Account => ({
  id,
  code: null,
  name,
  kind,
  allow_negative: false,
  active: true,
  balance: "0.00",
  overdrawn: false,
  overdrawn_label: null,
  ...changes,
});

const ACCOUNTS: Account[] = [
  account(1, "Caja principal", "CASH_BOX", { code: "main_cash", balance: "100000.00" }),
  account(2, "Caja vieja", "CASH_BOX", { active: false }),
  account(3, "Banco", "BANK", { code: "bank", balance: "500000.00" }),
  account(4, "Saldo inicial", "OPENING_BALANCE", { code: "opening_balance", allow_negative: true }),
  account(10, "Gastos operativos", "EXPENSE", { code: "expense", allow_negative: true }),
  account(11, "Pendientes de cobro", "PENDING_COLLECTIONS", { code: "pending_collections" }),
  account(6, "Juan Socio", "OWNER_EQUITY", { allow_negative: true }),
];

const CATEGORIES = [
  { id: 1, name: "Peajes", active: true, code: null },
  { id: 3, name: "Combustible", active: true, code: "FUEL" },
];

const VEHICLES = [
  {
    id: 21,
    plate: "AA123BB",
    plate_display: "AA 123 BB",
    description: "Camión norte",
    active: true,
    created_at: "2026-10-01T10:00:00Z",
    created_by_name: "Admin",
    updated_at: "2026-10-01T10:00:00Z",
    updated_by_name: "Admin",
  },
];

const OPERATION: Operation = {
  id: 40,
  type: "DIRECT_EXPENSE",
  business_date: "2026-10-09",
  description: "Gasto directo n.º 7 · Combustible",
  created_by: 9,
  created_by_name: "Oscar Operador",
  created_at: "2026-10-09T15:00:00Z",
  source_type: "expense",
  source_id: 7,
  reverses_operation_id: null,
  reversed_by_operation_id: null,
  entries: [
    { account_id: 1, account_name: "Caja principal", account_kind: "CASH_BOX", amount: "-50000.00" },
    { account_id: 10, account_name: "Gastos operativos", account_kind: "EXPENSE", amount: "50000.00" },
  ],
};

const expense = (changes: Partial<ExpenseDetail> = {}): ExpenseDetail => ({
  id: 7,
  origin: "DIRECT",
  state: "IN_EFFECT",
  business_date: "2026-10-09",
  category_id: 3,
  category_name: "Combustible",
  category_code: "FUEL",
  amount: "50000.00",
  description: "Carga en ruta",
  settlement_id: null,
  paid_from_account_id: 1,
  paid_from_account_name: "Caja principal",
  paid_from_account_kind: "CASH_BOX",
  operation_id: 40,
  reversal_operation_id: null,
  fuel: {
    id: 41,
    vehicle_id: 21,
    vehicle_plate: "AA 123 BB",
    vehicle_description: "Camión norte",
    vehicle_active: true,
    employee_id: 4,
    employee_name: "Juan Pérez",
    liters: "40.000",
    price_per_liter: "1250.0000",
    odometer_km: 120500,
    created_by_name: "Oscar Operador",
    updated_at: "2026-10-09T15:00:00Z",
    updated_by_name: "Oscar Operador",
  },
  legacy_fuel_without_detail: false,
  created_by: 9,
  created_by_name: "Oscar Operador",
  created_at: "2026-10-09T15:00:00Z",
  ended_by_name: null,
  ended_at: null,
  end_reason: null,
  operation: OPERATION,
  reversal: null,
  history: [],
  ...changes,
});

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;
type Role = "viewer" | "operator" | "admin";

const me = (role: Role) => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "settlements", name: "Rendiciones", role }],
});

function mockApi(routes: Record<string, Handler>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const handler = routes[`${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`];
    if (!handler) return jsonResponse(404, { code: "not_found", message: "x", details: null, request_id: null });
    return handler(init);
  });
}

const routes = (role: Role, extra: Record<string, Handler> = {}): Record<string, Handler> => ({
  "GET /auth/me": () => jsonResponse(200, me(role)),
  "GET /settlements/accounts": () => jsonResponse(200, ACCOUNTS),
  "GET /settlements/accounts/1/movements": () => jsonResponse(200, { items: [], total: 0 }),
  "GET /settlements/cheques/summary": () =>
    jsonResponse(200, { portfolio_count: 0, portfolio_amount: "0.00", in_transit_count: 0, in_transit_amount: "0.00" }),
  "GET /settlements/expense-categories": () => jsonResponse(200, CATEGORIES),
  "GET /vehicles": () => jsonResponse(200, VEHICLES),
  "GET /settlements/employees": () => jsonResponse(200, [{ id: 4, full_name: "Juan Pérez" }]),
  ...extra,
});

const posts = (spy: ReturnType<typeof mockApi>, path: string) =>
  spy.mock.calls
    .filter(
      ([input, init]) => new URL(String(input), "http://l").pathname === `/api/v1${path}` && init?.method === "POST",
    )
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

const apiError = (status: number, code: string, details: unknown = null) =>
  jsonResponse(status, { code, message: "Texto del backend.", details, request_id: "r" });

async function openExpense() {
  await userEvent.click(await screen.findByRole("button", { name: "Registrar gasto" }));
  return screen.getByRole("dialog");
}

describe("Registrar gasto from Cajas (R5-3)", () => {
  it("is hidden from a viewer", async () => {
    mockApi(routes("viewer"));
    renderWithQuery(<CashBoard />);
    await screen.findByText("Caja principal");
    expect(screen.queryByRole("button", { name: "Registrar gasto" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver gastos directos" })).toHaveAttribute(
      "href",
      "/rendiciones/cajas/gastos",
    );
  });

  it("offers only cash boxes and banks in effect as the paying account", async () => {
    mockApi(routes("operator"));
    renderWithQuery(<CashBoard />);
    const dialog = await openExpense();
    const options = within(within(dialog).getByLabelText("Cuenta pagadora"))
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(options).toEqual([
      "Elegí la caja o el banco…",
      "Caja principal · saldo $ 100.000,00",
      "Banco · saldo $ 500.000,00",
    ]);
  });

  it("shows the debit and the credit before sending, and sends only after the explicit confirmation", async () => {
    const spy = mockApi(
      routes("operator", {
        "POST /settlements/expenses": () =>
          jsonResponse(201, expense({ category_id: 1, category_name: "Peajes", amount: "5000.00", fuel: null })),
      }),
    );
    renderWithQuery(<CashBoard />);
    const dialog = await openExpense();
    await userEvent.selectOptions(within(dialog).getByLabelText("Cuenta pagadora"), "1");
    await userEvent.selectOptions(within(dialog).getByLabelText("Categoría"), "1");
    await userEvent.type(within(dialog).getByLabelText("Importe"), "5.000");
    await userEvent.type(within(dialog).getByLabelText("Descripción (opcional)"), "Peaje autopista");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar gasto" }));

    const confirmation = await screen.findByTestId("expense-confirmation");
    expect(confirmation).toHaveTextContent("GastoPeajes");
    expect(confirmation).toHaveTextContent("Caja principal-$ 5.000,00");
    expect(confirmation).toHaveTextContent("Gastos operativos+$ 5.000,00");
    expect(confirmation).toHaveTextContent(`Fecha${todayIso().split("-").reverse().join("/")}`);
    expect(posts(spy, "/settlements/expenses")).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Confirmar y registrar" }));
    await waitFor(() => expect(posts(spy, "/settlements/expenses")).toHaveLength(1));
    const [body] = posts(spy, "/settlements/expenses");
    expect(body).toMatchObject({
      business_date: todayIso(),
      category_id: 1,
      paid_from_account_id: 1,
      amount: "5000.00",
      description: "Peaje autopista",
    });
    expect(body).not.toHaveProperty("fuel");
    expect(typeof body.client_request_id).toBe("string");
    expect(await screen.findByRole("status")).toHaveTextContent("Gasto registrado: Peajes por $ 5.000,00");
  });

  it("fuel: no amount to type, who loaded is chosen explicitly, the total is computed", async () => {
    const spy = mockApi(routes("operator", { "POST /settlements/expenses": () => jsonResponse(201, expense()) }));
    renderWithQuery(<CashBoard />);
    const dialog = await openExpense();
    await userEvent.selectOptions(within(dialog).getByLabelText("Cuenta pagadora"), "1");
    await userEvent.selectOptions(within(dialog).getByLabelText("Categoría"), "3");
    expect(within(dialog).queryByLabelText("Importe")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Quién cargó")).toHaveValue("");
    await userEvent.selectOptions(within(dialog).getByLabelText("Vehículo"), "21");
    await userEvent.type(within(dialog).getByLabelText("Litros"), "40");
    await userEvent.type(within(dialog).getByLabelText("Precio por litro"), "1.250");
    expect(within(dialog).getByText(/Total estimado \$ 50\.000,00/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar gasto" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Elegí quién cargó.");

    await waitFor(() => expect(within(dialog).getByRole("option", { name: "Juan Pérez" })).toBeInTheDocument());
    await userEvent.selectOptions(within(dialog).getByLabelText("Quién cargó"), "4");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar gasto" }));
    const confirmation = await screen.findByTestId("expense-confirmation");
    expect(confirmation).toHaveTextContent("GastoCombustible");
    expect(confirmation).toHaveTextContent("Caja principal-$ 50.000,00");
    expect(confirmation).toHaveTextContent("Gastos operativos+$ 50.000,00");
    expect(confirmation).toHaveTextContent("VehículoAA 123 BB · Camión norte");
    expect(confirmation).toHaveTextContent("EmpleadoJuan Pérez");

    await userEvent.click(screen.getByRole("button", { name: "Confirmar y registrar" }));
    await waitFor(() => expect(posts(spy, "/settlements/expenses")).toHaveLength(1));
    const [body] = posts(spy, "/settlements/expenses");
    expect(body).toMatchObject({
      category_id: 3,
      fuel: { vehicle_id: 21, employee_id: 4, liters: "40.000", price_per_liter: "1250.0000", odometer_km: null },
    });
    expect(body).not.toHaveProperty("amount");
  });

  it("refuses a future date before sending", async () => {
    const spy = mockApi(routes("operator"));
    renderWithQuery(<CashBoard />);
    const dialog = await openExpense();
    await userEvent.selectOptions(within(dialog).getByLabelText("Cuenta pagadora"), "1");
    await userEvent.selectOptions(within(dialog).getByLabelText("Categoría"), "1");
    await userEvent.type(within(dialog).getByLabelText("Importe"), "100");
    const date = within(dialog).getByLabelText("Fecha del gasto");
    await userEvent.clear(date);
    await userEvent.type(date, "2999-01-01");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar gasto" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("La fecha del gasto no puede ser futura.");
    expect(screen.queryByTestId("expense-confirmation")).not.toBeInTheDocument();
    expect(posts(spy, "/settlements/expenses")).toHaveLength(0);
  });

  it("keeps the confirmation open with the reason when the box has no funds", async () => {
    mockApi(
      routes("operator", {
        "POST /settlements/expenses": () =>
          apiError(409, "insufficient_funds", { account_id: 1, business_date: todayIso(), balance: "100000.00" }),
      }),
    );
    renderWithQuery(<CashBoard />);
    const dialog = await openExpense();
    await userEvent.selectOptions(within(dialog).getByLabelText("Cuenta pagadora"), "1");
    await userEvent.selectOptions(within(dialog).getByLabelText("Categoría"), "1");
    await userEvent.type(within(dialog).getByLabelText("Importe"), "200000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar gasto" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirmar y registrar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No hay saldo suficiente en Caja principal");
    expect(screen.getByRole("button", { name: "Volver a editar" })).toBeInTheDocument();
  });
});

describe("direct expense detail and list", () => {
  it("an admin can reverse (with a reason) and correct; the reversal keeps the expense date", async () => {
    const spy = mockApi(
      routes("admin", {
        "GET /settlements/expenses/7": () => jsonResponse(200, expense()),
        "POST /settlements/expenses/7/reverse": () => jsonResponse(201, expense({ state: "REVERSED" })),
      }),
    );
    renderWithQuery(<ExpenseDetailView expenseId={7} />);
    expect(await screen.findByTestId("expense-amount")).toHaveTextContent("$ 50.000,00");
    expect(screen.getByText("AA 123 BB · Camión norte")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Corregir descripción" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Revertir gasto" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("con la misma fecha del gasto (09/10/2026)");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir gasto" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Indicá el motivo.");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Se cargó en la caja equivocada");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir gasto" }));
    await waitFor(() => expect(posts(spy, "/settlements/expenses/7/reverse")).toHaveLength(1));
    const [body] = posts(spy, "/settlements/expenses/7/reverse");
    expect(body.reason).toBe("Se cargó en la caja equivocada");
    expect(Object.keys(body).sort()).toEqual(["client_request_id", "reason"]);
  });

  it("corrects only the description and the odometer that changed, with a reason", async () => {
    const spy = mockApi(
      routes("admin", {
        "GET /settlements/expenses/7": () => jsonResponse(200, expense()),
        "POST /settlements/expenses/7/descriptive": () => jsonResponse(200, expense()),
      }),
    );
    renderWithQuery(<ExpenseDetailView expenseId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Corregir descripción" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByLabelText("Importe")).not.toBeInTheDocument();
    const odometer = within(dialog).getByLabelText("Odómetro en km (opcional)");
    await userEvent.clear(odometer);
    await userEvent.type(odometer, "120600");
    await userEvent.type(within(dialog).getByLabelText("Motivo de la corrección"), "Error de tipeo");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar corrección" }));
    await waitFor(() => expect(posts(spy, "/settlements/expenses/7/descriptive")).toHaveLength(1));
    expect(posts(spy, "/settlements/expenses/7/descriptive")[0]).toEqual({
      reason: "Error de tipeo",
      odometer_km: 120600,
    });
  });

  it("an operator sees no admin actions, and a reversed expense offers none", async () => {
    mockApi(routes("operator", { "GET /settlements/expenses/7": () => jsonResponse(200, expense()) }));
    const { unmount } = renderWithQuery(<ExpenseDetailView expenseId={7} />);
    await screen.findByTestId("expense-amount");
    expect(screen.queryByRole("button", { name: "Revertir gasto" })).not.toBeInTheDocument();
    unmount();
    vi.restoreAllMocks();
    const reversal = { ...OPERATION, id: 41, reverses_operation_id: 40 };
    mockApi(
      routes("admin", {
        "GET /settlements/expenses/7": () =>
          jsonResponse(
            200,
            expense({
              state: "REVERSED",
              reversal_operation_id: 41,
              reversal,
              ended_by_name: "Ana Admin",
              ended_at: "2026-10-09T16:00:00Z",
              end_reason: "Duplicado",
            }),
          ),
      }),
    );
    renderWithQuery(<ExpenseDetailView expenseId={7} />);
    expect(await screen.findByTestId("expense-state")).toHaveTextContent("Revertido");
    expect(screen.getByText("Motivo: Duplicado")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revertir gasto" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Corregir descripción" })).not.toBeInTheDocument();
  });

  it("lists direct expenses with account, state, user and the fuel summary", async () => {
    const spy = mockApi(
      routes("viewer", {
        "GET /settlements/expenses": () => jsonResponse(200, { items: [expense()], total: 1 }),
      }),
    );
    renderWithQuery(<ExpenseList />);
    const row = await screen.findByTestId("expense-row-7");
    expect(row).toHaveTextContent("09/10/2026");
    expect(row).toHaveTextContent("Combustible");
    expect(row).toHaveTextContent("AA 123 BB · 40 l · Juan Pérez");
    expect(row).toHaveTextContent("$ 50.000,00");
    expect(row).toHaveTextContent("Caja principal");
    expect(row).toHaveTextContent("Vigente");
    expect(row).toHaveTextContent("Oscar Operador");
    expect(screen.queryByRole("button", { name: "Registrar gasto" })).not.toBeInTheDocument();
    const listCall = spy.mock.calls.find(([input]) => String(input).includes("/settlements/expenses?"));
    expect(String(listCall?.[0])).toContain("origin=DIRECT");
  });
});

describe("the DIRECT_EXPENSE operation in Cajas", () => {
  it("is never reversed from Cajas: it points to its expense", async () => {
    mockApi(routes("admin", { "GET /settlements/operations/40": () => jsonResponse(200, OPERATION) }));
    renderWithQuery(<OperationDetail operationId={40} />);
    expect(await screen.findByText(/se revierte desde el\s+gasto/)).toBeInTheDocument();
    for (const link of screen.getAllByRole("link", { name: "Gasto directo #7" })) {
      expect(link).toHaveAttribute("href", "/rendiciones/cajas/gastos/7");
    }
    expect(screen.queryByRole("button", { name: "Revertir operación" })).not.toBeInTheDocument();
  });

  it("reads the expense errors and the legacy fuel summary", () => {
    const error = new ApiError(409, { code: "idempotency_conflict", message: "x", details: null, request_id: "r" });
    expect(expenseErrorView(error).conflict).toBe(true);
    const own = new ApiError(422, {
      code: "invalid_payment_account",
      message: "Elegí una caja o el banco.",
      details: null,
      request_id: "r",
    });
    expect(expenseErrorView(own).message).toBe("Elegí una caja o el banco.");
    expect(fuelSummary({ fuel: null, legacy_fuel_without_detail: true })).toContain("Sin detalle");
    expect(fuelSummary({ fuel: null, legacy_fuel_without_detail: false })).toBeNull();
  });
});
