import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { parseAmount } from "@/modules/settlements/cash/amount";
import { CashBoard } from "@/modules/settlements/cash/cash-board";
import { cashErrorView } from "@/modules/settlements/cash/errors";
import { OperationDetail } from "@/modules/settlements/cash/operation-detail";
import { newRequestId } from "@/modules/settlements/cash/request-id";
import type { Account, Movement, Operation } from "@/modules/settlements/cash/api";
import { ApiError } from "@/lib/api/client";
import { formatMoney, todayIso } from "@/lib/format";

import { jsonResponse, renderWithQuery } from "./render";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

const ACCOUNTS: Account[] = [
  {
    id: 1,
    code: "main_cash",
    name: "Caja principal",
    kind: "CASH_BOX",
    allow_negative: false,
    active: true,
    balance: "150000.00",
  },
  {
    id: 2,
    code: "change_cash",
    name: "Caja de cambio",
    kind: "CASH_BOX",
    allow_negative: false,
    active: true,
    balance: "0.00",
  },
  { id: 3, code: "bank", name: "Banco", kind: "BANK", allow_negative: false, active: true, balance: "1234567.89" },
  {
    id: 4,
    code: "opening_balance",
    name: "Saldo inicial",
    kind: "OPENING_BALANCE",
    allow_negative: true,
    active: true,
    balance: "-1384567.89",
  },
  {
    id: 5,
    code: "adjustment",
    name: "Ajustes",
    kind: "ADJUSTMENT",
    allow_negative: true,
    active: true,
    balance: "0.00",
  },
  {
    id: 8,
    code: "delivery_collections",
    name: "Cobranzas de reparto",
    kind: "DELIVERY_COLLECTIONS",
    allow_negative: true,
    active: true,
    balance: "-100000.00",
  },
  {
    id: 9,
    code: null,
    name: "Cuenta interna de Pedro Gómez (#4)",
    kind: "EMPLOYEE",
    allow_negative: true,
    active: true,
    balance: "1000.00",
  },
  {
    id: 6,
    code: null,
    name: "Juan Pérez",
    kind: "OWNER_EQUITY",
    allow_negative: true,
    active: true,
    balance: "-70000.00",
  },
  {
    id: 7,
    code: null,
    name: "Socia vieja",
    kind: "OWNER_EQUITY",
    allow_negative: true,
    active: false,
    balance: "0.00",
  },
];

const MOVEMENT: Movement = {
  entry_id: 10,
  operation_id: 21,
  operation_type: "CASH_TRANSFER",
  business_date: "2026-10-02",
  description: "Cambio para repartos",
  amount: "-2500.50",
  created_by: 4,
  created_by_name: "Oscar Operador",
  created_at: "2026-10-02T14:05:00Z",
  source_type: null,
  source_id: null,
  reverses_operation_id: null,
  reversed_by_operation_id: 22,
};

const OPERATION: Operation = {
  id: 30,
  type: "OPENING_BALANCE",
  business_date: "2026-10-01",
  description: null,
  created_by: 3,
  created_by_name: "Ana Admin",
  created_at: "2026-10-01T12:00:00Z",
  source_type: null,
  source_id: null,
  reverses_operation_id: null,
  reversed_by_operation_id: null,
  entries: [
    { account_id: 1, account_name: "Caja principal", account_kind: "CASH_BOX", amount: "150000.00" },
    { account_id: 4, account_name: "Saldo inicial", account_kind: "OPENING_BALANCE", amount: "-150000.00" },
  ],
};

type Handler = (init: RequestInit | undefined, call: number) => Response | Promise<Response>;

const me = (role: "viewer" | "operator" | "admin") => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "settlements", name: "Rendiciones", role }],
});

/** fetch mock routed by "METHOD /path" (query string ignored); records every call. */
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

function boardRoutes(role: "viewer" | "operator" | "admin", extra: Record<string, Handler> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, me(role)),
    "GET /settlements/accounts": () => jsonResponse(200, ACCOUNTS),
    "GET /settlements/accounts/1/movements": () => jsonResponse(200, { items: [MOVEMENT], total: 1 }),
    ...extra,
  };
}

const bodies = (spy: ReturnType<typeof mockApi>, path: string) =>
  spy.mock.calls
    .filter(([input, init]) => String(input).startsWith(`/api/v1${path}`) && init?.method === "POST")
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

const apiError = (status: number, code: string, details: unknown = null) =>
  jsonResponse(status, { code, message: "texto interno", details, request_id: "req-1" });

describe("money and amounts", () => {
  it("formats API decimals the Argentine way without floats", () => {
    expect(formatMoney("1234567.89")).toBe("$ 1.234.567,89");
    expect(formatMoney("-70000")).toBe("-$ 70.000,00");
    expect(formatMoney("2500.5", { signed: true })).toBe("+$ 2.500,50");
    expect(formatMoney("0.00", { signed: true })).toBe("$ 0,00");
    expect(formatMoney("999999999999.99")).toBe("$ 999.999.999.999,99");
  });

  it("reads amounts typed with Argentine separators", () => {
    expect(parseAmount("1.500,50")).toBe("1500.50");
    expect(parseAmount("1500,5")).toBe("1500.50");
    expect(parseAmount("1500.25")).toBe("1500.25");
    expect(parseAmount("$ 2.000")).toBe("2000.00");
    expect(parseAmount("1.500.000")).toBe("1500000.00");
    for (const bad of ["", "0", "0,00", "-5", "1,555", "abc", "1.5.5"]) expect(parseAmount(bad)).toBeNull();
  });

  it("builds a v4 request id even without crypto.randomUUID (plain HTTP)", () => {
    const original = Object.getOwnPropertyDescriptor(crypto, "randomUUID");
    Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });
    try {
      expect(newRequestId()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    } finally {
      if (original) Object.defineProperty(crypto, "randomUUID", original);
      else delete (crypto as { randomUUID?: unknown }).randomUUID;
    }
  });
});

describe("Cajas error messages", () => {
  const names = (id: number) => ACCOUNTS.find((a) => a.id === id)?.name;
  const error = (status: number, code: string, details: unknown = null) =>
    new ApiError(status, { code, message: "SQLSTATE 23514 settlements.accounts", details, request_id: "r" });

  it("explains each business error without technical text", () => {
    const today = { account_id: 1, business_date: "2026-10-04", balance: "100.00", amount: "-500.00" };
    expect(cashErrorView(error(409, "insufficient_funds", today), names, "2026-10-04").message).toBe(
      "No hay saldo suficiente en Caja principal para realizar este movimiento. Saldo disponible al 04/10/2026: $ 100,00.",
    );
    const past = { ...today, business_date: "2026-10-02", balance: "20000.00" };
    expect(cashErrorView(error(409, "insufficient_funds", past), names, "2026-10-01").message).toContain(
      "el 02/10/2026 quedaría en negativo (saldo de ese día: $ 20.000,00)",
    );
    expect(cashErrorView(error(409, "idempotency_conflict"), names).conflict).toBe(true);
    expect(cashErrorView(error(409, "operation_already_reversed")).message).toBe("Esta operación ya fue revertida.");
    expect(cashErrorView(error(409, "operation_owned_by_document")).message).toContain("reabriendo la rendición");
    expect(cashErrorView(error(409, "account_inactive")).message).toContain("dada de baja");
    expect(cashErrorView(error(409, "account_name_taken", { account_id: 7, active: false }), names).message).toBe(
      "Ya existe la cuenta «Socia vieja», dada de baja. No se crea otra con un nombre parecido.",
    );
    expect(cashErrorView(error(422, "invalid_legs")).message).toBe(
      "Las cuentas elegidas no corresponden a esta operación.",
    );
    expect(cashErrorView(error(422, "future_business_date")).message).toContain("posterior a hoy");
    expect(cashErrorView(error(422, "reason_required")).message).toBe("Indicá el motivo.");
    for (const code of ["invalid_ledger_operation", "something_new", "validation_error"]) {
      expect(cashErrorView(error(409, code)).message).not.toMatch(/SQLSTATE|settlements|_/);
    }
  });

  it("treats network errors and 5xx as uncertain: retry with the same request id", () => {
    expect(
      cashErrorView(new ApiError(0, { code: "network_error", message: "", details: null, request_id: null })).uncertain,
    ).toBe(true);
    expect(cashErrorView(error(502, "http_error")).uncertain).toBe(true);
    expect(cashErrorView(error(409, "insufficient_funds")).uncertain).toBeUndefined();
  });
});

describe("CashBoard", () => {
  it("shows cash boxes, bank and partner accounts with backend balances, never the technical accounts", async () => {
    mockApi(boardRoutes("viewer"));
    renderWithQuery(<CashBoard />);
    const main = await screen.findByRole("button", { name: "Ver movimientos de Caja principal" });
    expect(main).toHaveTextContent("$ 150.000,00");
    expect(main).toHaveTextContent("Caja");
    expect(screen.getByRole("button", { name: "Ver movimientos de Banco" })).toHaveTextContent("$ 1.234.567,89");
    expect(screen.getByRole("button", { name: "Ver movimientos de Juan Pérez" })).toHaveTextContent(
      "Aportado neto$ 70.000,00",
    );
    expect(screen.getByRole("button", { name: "Ver movimientos de Socia vieja" })).toHaveTextContent("Dada de baja");
    expect(screen.queryByText("Saldo inicial")).not.toBeInTheDocument();
    expect(screen.queryByText("Ajustes")).not.toBeInTheDocument();
    expect(screen.queryByText("Cobranzas de reparto")).not.toBeInTheDocument();
    expect(screen.queryByText(/Cuenta interna de/)).not.toBeInTheDocument();
  });

  it("shows loading, then the error with a retry", async () => {
    let fail = true;
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("viewer")),
      "GET /settlements/accounts": async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
        return fail ? apiError(500, "internal_error") : jsonResponse(200, ACCOUNTS);
      },
      "GET /settlements/accounts/1/movements": () => jsonResponse(200, { items: [], total: 0 }),
    });
    renderWithQuery(<CashBoard />);
    expect(screen.getByLabelText("Cargando cuentas")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("Esta cuenta todavía no tiene movimientos.")).toBeInTheDocument();
  });

  it("lists movements newest first with author, signed amount, origin and reversed state", async () => {
    const spy = mockApi(boardRoutes("viewer"));
    renderWithQuery(<CashBoard />);
    const row = (await screen.findByText("Cambio para repartos")).closest("tr")!;
    expect(row).toHaveTextContent("02/10/2026");
    expect(row).toHaveTextContent("Traspaso entre cajas");
    expect(row).toHaveTextContent("Revertida");
    expect(row).toHaveTextContent("-$ 2.500,50");
    expect(row).toHaveTextContent("Oscar Operador");
    expect(row).toHaveTextContent("Carga manual");
    expect(within(row).getByRole("link", { name: /Ver detalle/ })).toHaveAttribute(
      "href",
      "/rendiciones/cajas/operaciones/21",
    );
    expect(String(spy.mock.calls.find(([u]) => String(u).includes("movements"))![0])).toContain("limit=20&offset=0");
  });

  it("renders only the actions the role allows", async () => {
    mockApi(boardRoutes("viewer"));
    const { unmount } = renderWithQuery(<CashBoard />);
    await screen.findByText("Cambio para repartos");
    expect(screen.queryByRole("button", { name: /Mover entre cajas/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Operaciones administrativas")).not.toBeInTheDocument();
    unmount();
    vi.restoreAllMocks();

    mockApi(boardRoutes("operator"));
    const operator = renderWithQuery(<CashBoard />);
    expect(await screen.findByRole("button", { name: /Mover entre cajas/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Depositar en banco/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Extraer del banco/ })).toBeInTheDocument();
    expect(screen.queryByText("Operaciones administrativas")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ajuste administrativo" })).not.toBeInTheDocument();
    operator.unmount();
    vi.restoreAllMocks();

    mockApi(boardRoutes("admin"));
    renderWithQuery(<CashBoard />);
    expect(await screen.findByText("Operaciones administrativas")).toBeInTheDocument();
    for (const name of [
      "Cargar saldo inicial",
      "Registrar aporte de socio",
      "Registrar devolución a socio",
      "Ajuste administrativo",
      /Crear cuenta de aporte/,
    ]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("sends a transfer with its request id and business date, then reloads balances from the API", async () => {
    const created = {
      ...OPERATION,
      id: 40,
      type: "CASH_TRANSFER" as const,
      entries: [
        { account_id: 1, account_name: "Caja principal", account_kind: "CASH_BOX" as const, amount: "-1500.50" },
        { account_id: 2, account_name: "Caja de cambio", account_kind: "CASH_BOX" as const, amount: "1500.50" },
      ],
    };
    const spy = mockApi(
      boardRoutes("operator", { "POST /settlements/operations/cash-transfer": () => jsonResponse(201, created) }),
    );
    renderWithQuery(<CashBoard />);
    await userEvent.click(await screen.findByRole("button", { name: /Mover entre cajas/ }));
    const dialog = screen.getByRole("dialog", { name: "Mover entre cajas" });
    await userEvent.type(within(dialog).getByLabelText("Importe"), "1.500,50");
    expect(within(dialog).getByText("Se registran $ 1.500,50")).toBeInTheDocument();
    const accountsBefore = spy.mock.calls.filter(([u]) => String(u).endsWith("/accounts?include_inactive=true")).length;
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar traspaso" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Operación registrada: Traspaso entre cajas por $ 1.500,50.",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const [body] = bodies(spy, "/settlements/operations/cash-transfer");
    expect(body).toEqual({
      client_request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
      business_date: todayIso(),
      description: null,
      from_account_id: 1,
      to_account_id: 2,
      amount: "1500.50",
    });
    await waitFor(() =>
      expect(
        spy.mock.calls.filter(([u]) => String(u).endsWith("/accounts?include_inactive=true")).length,
      ).toBeGreaterThan(accountsBefore),
    );
  });

  it("keeps the same request id when retrying after a network error, and a new one for the next operation", async () => {
    const spy = mockApi(
      boardRoutes("operator", {
        "POST /settlements/operations/bank-deposit": (_init, call) => {
          if (call === 1) throw new TypeError("Failed to fetch");
          return jsonResponse(call === 2 ? 200 : 201, { ...OPERATION, id: 41, type: "BANK_DEPOSIT" });
        },
      }),
    );
    renderWithQuery(<CashBoard />);
    await userEvent.click(await screen.findByRole("button", { name: /Depositar en banco/ }));
    let dialog = screen.getByRole("dialog", { name: "Depositar en banco" });
    await userEvent.type(within(dialog).getByLabelText("Importe"), "500");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar depósito" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("no se va a duplicar");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reintentar" }));
    await screen.findByRole("status");

    await userEvent.click(screen.getByRole("button", { name: /Depositar en banco/ }));
    dialog = screen.getByRole("dialog", { name: "Depositar en banco" });
    await userEvent.type(within(dialog).getByLabelText("Importe"), "500");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar depósito" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const ids = bodies(spy, "/settlements/operations/bank-deposit").map((body) => body.client_request_id);
    expect(ids).toHaveLength(3);
    expect(ids[1]).toBe(ids[0]);
    expect(ids[2]).not.toBe(ids[0]);
  });

  it("keeps an uncertain operation after the form is closed and retries it with the same id and body", async () => {
    const spy = mockApi(
      boardRoutes("operator", {
        "POST /settlements/operations/cash-transfer": (_init, call) => {
          if (call === 1) throw new TypeError("Failed to fetch");
          return jsonResponse(200, { ...OPERATION, id: 50, type: "CASH_TRANSFER" });
        },
      }),
    );
    renderWithQuery(<CashBoard />);
    await userEvent.click(await screen.findByRole("button", { name: /Mover entre cajas/ }));
    let dialog = screen.getByRole("dialog", { name: "Mover entre cajas" });
    await userEvent.type(within(dialog).getByLabelText("Importe"), "1.500");
    await userEvent.type(within(dialog).getByLabelText("Descripción (opcional)"), "Cambio");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar traspaso" }));
    await within(dialog).findByRole("alert");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));

    // The board keeps it in sight.
    const banner = screen.getByRole("region", { name: "Operaciones sin confirmar" });
    expect(banner).toHaveTextContent("Mover entre cajas · $ 1.500,00 · Caja principal → Caja de cambio");

    // Reopening the form offers the stored attempt instead of a blank form with a new id.
    await userEvent.click(screen.getByRole("button", { name: /Mover entre cajas/ }));
    dialog = screen.getByRole("dialog", { name: "Mover entre cajas" });
    expect(dialog).toHaveTextContent("Hay una operación sin confirmar");
    expect(within(dialog).queryByLabelText("Importe")).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Reintentar la misma operación" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Operación registrada");

    const sent = bodies(spy, "/settlements/operations/cash-transfer");
    expect(sent).toHaveLength(2);
    expect(sent[1]).toEqual(sent[0]);
    expect(sessionStorage.length).toBe(0);
    expect(screen.queryByRole("region", { name: "Operaciones sin confirmar" })).not.toBeInTheDocument();
  });

  it("abandons an uncertain operation only after a warning, and then uses a new id", async () => {
    const spy = mockApi(
      boardRoutes("operator", {
        "POST /settlements/operations/bank-deposit": (_init, call) =>
          call === 1 ? apiError(503, "http_error") : jsonResponse(201, { ...OPERATION, type: "BANK_DEPOSIT" }),
      }),
    );
    renderWithQuery(<CashBoard />);
    await userEvent.click(await screen.findByRole("button", { name: /Depositar en banco/ }));
    let dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Importe"), "500");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar depósito" }));
    await within(dialog).findByRole("alert");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cerrar" }));

    await userEvent.click(screen.getByRole("button", { name: /Depositar en banco/ }));
    dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Descartar…" }));
    expect(within(dialog).getByRole("alertdialog")).toHaveTextContent("el movimiento quedaría duplicado");
    expect(sessionStorage.length).toBe(1);
    await userEvent.click(within(dialog).getByRole("button", { name: "Descartar el intento" }));
    expect(sessionStorage.length).toBe(0);

    await userEvent.type(within(dialog).getByLabelText("Importe"), "700");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar depósito" }));
    await screen.findByRole("status");
    const ids = bodies(spy, "/settlements/operations/bank-deposit").map((body) => body.client_request_id);
    expect(ids).toHaveLength(2);
    expect(ids[1]).not.toBe(ids[0]);
  });

  it("never mixes stored attempts of different operations or users, and keeps nothing sensitive", async () => {
    mockApi(
      boardRoutes("operator", {
        "POST /settlements/operations/cash-transfer": () => {
          throw new TypeError("Failed to fetch");
        },
      }),
    );
    renderWithQuery(<CashBoard />);
    await userEvent.click(await screen.findByRole("button", { name: /Mover entre cajas/ }));
    let dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Importe"), "10");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar traspaso" }));
    await within(dialog).findByRole("alert");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));

    const stored = JSON.parse(sessionStorage.getItem("districom:settlements:pending:cash-transfer")!) as Record<
      string,
      unknown
    >;
    expect(Object.keys(stored).sort()).toEqual(["body", "path", "savedAt", "slot", "summary", "userId"]);
    expect(Object.keys(stored.body as object).sort()).toEqual([
      "amount",
      "business_date",
      "client_request_id",
      "description",
      "from_account_id",
      "to_account_id",
    ]);

    // Another operation opens blank.
    await userEvent.click(screen.getByRole("button", { name: /Depositar en banco/ }));
    dialog = screen.getByRole("dialog", { name: "Depositar en banco" });
    expect(within(dialog).getByLabelText("Importe")).toBeInTheDocument();
    expect(dialog).not.toHaveTextContent("sin confirmar");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));

    // Another user in the same tab does not get it.
    sessionStorage.setItem("districom:settlements:pending:cash-transfer", JSON.stringify({ ...stored, userId: 999 }));
    await userEvent.click(screen.getByRole("button", { name: /Mover entre cajas/ }));
    expect(within(screen.getByRole("dialog")).getByLabelText("Importe")).toBeInTheDocument();
  });

  it("forgets the attempt when the retry gets a definitive answer", async () => {
    mockApi(
      boardRoutes("operator", {
        "POST /settlements/operations/cash-transfer": (_init, call) =>
          call === 1
            ? apiError(504, "http_error")
            : apiError(409, "insufficient_funds", {
                account_id: 1,
                business_date: todayIso(),
                balance: "5.00",
                amount: "-10.00",
              }),
      }),
    );
    renderWithQuery(<CashBoard />);
    await userEvent.click(await screen.findByRole("button", { name: /Mover entre cajas/ }));
    let dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Importe"), "10");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar traspaso" }));
    await within(dialog).findByRole("alert");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    await userEvent.click(screen.getByRole("button", { name: /Mover entre cajas/ }));
    dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reintentar la misma operación" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("No hay saldo suficiente en Caja principal");
    expect(sessionStorage.length).toBe(0);
  });

  it("blocks a second submit while waiting for the answer", async () => {
    let release: (response: Response) => void = () => {};
    const spy = mockApi(
      boardRoutes("operator", {
        "POST /settlements/operations/cash-transfer": () => new Promise<Response>((resolve) => (release = resolve)),
      }),
    );
    renderWithQuery(<CashBoard />);
    await userEvent.click(await screen.findByRole("button", { name: /Mover entre cajas/ }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Importe"), "10");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar traspaso" }));
    const pending = await within(dialog).findByRole("button", { name: "Registrando…" });
    expect(pending).toBeDisabled();
    await userEvent.click(pending);
    expect(bodies(spy, "/settlements/operations/cash-transfer")).toHaveLength(1);
    release(jsonResponse(201, { ...OPERATION, type: "CASH_TRANSFER" }));
    await screen.findByRole("status");
  });

  it("shows the backend's business errors in plain words and lets a conflict start over", async () => {
    const spy = mockApi(
      boardRoutes("operator", {
        "POST /settlements/operations/cash-transfer": (_init, call) =>
          call === 1
            ? apiError(409, "insufficient_funds", {
                account_id: 1,
                business_date: todayIso(),
                balance: "150000.00",
                amount: "-999999.00",
              })
            : apiError(409, "idempotency_conflict", { operation_id: 3 }),
      }),
    );
    renderWithQuery(<CashBoard />);
    await userEvent.click(await screen.findByRole("button", { name: /Mover entre cajas/ }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Importe"), "999999");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar traspaso" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "No hay saldo suficiente en Caja principal para realizar este movimiento.",
    );
    expect(within(dialog).getByRole("alert")).not.toHaveTextContent("texto interno");

    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar traspaso" }));
    expect(await within(dialog).findByText(/ya se había registrado con otros datos/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Empezar una operación nueva" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar traspaso" }));
    const ids = bodies(spy, "/settlements/operations/cash-transfer").map((body) => body.client_request_id);
    expect(ids[1]).toBe(ids[0]);
    expect(ids[2]).not.toBe(ids[1]);
  });

  it("validates the form before sending", async () => {
    const spy = mockApi(boardRoutes("operator"));
    renderWithQuery(<CashBoard />);
    await userEvent.click(await screen.findByRole("button", { name: /Mover entre cajas/ }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Importe"), "1,555");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar traspaso" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Ingresá un importe mayor a cero");
    await userEvent.selectOptions(within(dialog).getByLabelText("Hacia"), "1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar traspaso" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Elegí dos cajas distintas.");
    expect(bodies(spy, "/settlements/operations/cash-transfer")).toHaveLength(0);
  });

  it("sends an adjustment with its sign and a mandatory reason, set apart as administrative", async () => {
    const spy = mockApi(
      boardRoutes("admin", {
        "POST /settlements/operations/adjustment": () => jsonResponse(201, { ...OPERATION, type: "ADJUSTMENT" }),
      }),
    );
    renderWithQuery(<CashBoard />);
    await userEvent.click(await screen.findByRole("button", { name: "Ajuste administrativo" }));
    const dialog = screen.getByRole("dialog", { name: "Ajuste administrativo" });
    expect(dialog).toHaveTextContent("Corrección administrativa, no un movimiento de caja");
    expect(dialog).toHaveTextContent("Requiere administrador y motivo, queda auditada");
    await userEvent.selectOptions(within(dialog).getByLabelText("Cuenta"), "1");
    await userEvent.click(within(dialog).getByLabelText("Faltante (resta del saldo)"));
    await userEvent.type(within(dialog).getByLabelText("Importe"), "250");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar ajuste" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Indicá el motivo.");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Faltante en arqueo");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar ajuste" }));
    await screen.findByRole("status");
    expect(bodies(spy, "/settlements/operations/adjustment")[0]).toMatchObject({
      account_id: 1,
      amount: "-250.00",
      description: "Faltante en arqueo",
    });
  });

  it("creates a partner account and says so when an equivalent one already exists", async () => {
    mockApi(
      boardRoutes("admin", {
        "POST /settlements/accounts": () => apiError(409, "account_name_taken", { account_id: 6, active: true }),
      }),
    );
    renderWithQuery(<CashBoard />);
    await userEvent.click(await screen.findByRole("button", { name: /Crear cuenta de aporte/ }));
    const dialog = screen.getByRole("dialog", { name: "Crear cuenta de aporte" });
    await userEvent.type(within(dialog).getByLabelText("Nombre del socio"), "juan perez");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear cuenta" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Ya existe la cuenta «Juan Pérez». No se crea otra con un nombre parecido.",
    );
  });
});

describe("OperationDetail", () => {
  it("shows the ledger legs, technical accounts included, and the author", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("viewer")),
      "GET /settlements/operations/30": () => jsonResponse(200, OPERATION),
    });
    renderWithQuery(<OperationDetail operationId={30} />);
    expect(await screen.findByRole("heading", { name: "Saldo inicial n.º 30" })).toBeInTheDocument();
    expect(screen.getByText("Ana Admin")).toBeInTheDocument();
    expect(screen.getByText("01/10/2026")).toBeInTheDocument();
    const legs = screen
      .getAllByRole("row")
      .slice(1)
      .map((row) => row.textContent);
    expect(legs).toEqual(["Caja principalCaja+$ 150.000,00", "Saldo inicialCuenta técnica-$ 150.000,00"]);
    expect(screen.queryByRole("button", { name: "Revertir operación" })).not.toBeInTheDocument();
  });

  it("shows a settlement close with its settlement and never offers to reverse it", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("admin")),
      "GET /settlements/operations/30": () =>
        jsonResponse(200, {
          ...OPERATION,
          type: "SETTLEMENT_CLOSE",
          source_type: "settlement",
          source_id: 7,
          entries: [
            { account_id: 1, account_name: "Caja principal", account_kind: "CASH_BOX", amount: "99000.00" },
            {
              account_id: 9,
              account_name: "Cuenta interna de Pedro Gómez (#4)",
              account_kind: "EMPLOYEE",
              amount: "1000.00",
            },
            {
              account_id: 8,
              account_name: "Cobranzas de reparto",
              account_kind: "DELIVERY_COLLECTIONS",
              amount: "-100000.00",
            },
          ],
        }),
    });
    renderWithQuery(<OperationDetail operationId={30} />);
    expect(await screen.findByRole("heading", { name: "Cierre de rendición n.º 30" })).toBeInTheDocument();
    expect(screen.getByText(/pertenece a la Rendición #7: se corrige reabriendo la rendición/)).toBeInTheDocument();
    const legs = screen
      .getAllByRole("row")
      .slice(1)
      .map((row) => row.textContent);
    expect(legs).toEqual([
      "Caja principalCaja+$ 99.000,00",
      "Cuenta interna de Pedro Gómez (#4)Cuenta interna de empleado+$ 1.000,00",
      "Cobranzas de repartoCuenta técnica-$ 100.000,00",
    ]);
    expect(screen.queryByRole("button", { name: "Revertir operación" })).not.toBeInTheDocument();
  });

  it("marks a reversed operation and links both ways", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("admin")),
      "GET /settlements/operations/30": () => jsonResponse(200, { ...OPERATION, reversed_by_operation_id: 31 }),
    });
    renderWithQuery(<OperationDetail operationId={30} />);
    expect(await screen.findByText("Esta operación fue revertida y ya no afecta los saldos.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver el reverso n.º 31" })).toHaveAttribute(
      "href",
      "/rendiciones/cajas/operaciones/31",
    );
    expect(screen.queryByRole("button", { name: "Revertir operación" })).not.toBeInTheDocument();
  });

  it("reverts with confirmation and a mandatory reason, never as a deletion", async () => {
    let reversed = false;
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("admin")),
      "GET /settlements/operations/30": () =>
        jsonResponse(200, { ...OPERATION, reversed_by_operation_id: reversed ? 31 : null }),
      "POST /settlements/operations/30/reverse": () => {
        reversed = true;
        return jsonResponse(201, { ...OPERATION, id: 31, type: "REVERSAL", reverses_operation_id: 30 });
      },
    });
    renderWithQuery(<OperationDetail operationId={30} />);
    await userEvent.click(await screen.findByRole("button", { name: "Revertir operación" }));
    const dialog = screen.getByRole("dialog", { name: "Revertir operación" });
    expect(dialog).toHaveTextContent("El movimiento original no se borra.");
    expect(dialog).toHaveTextContent("Se crea un movimiento inverso");
    expect(dialog).toHaveTextContent("historial");
    expect(screen.queryByText(/Eliminar/)).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir operación" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Indicá el motivo.");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Cargado dos veces");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir operación" }));
    expect(await screen.findByText(/Se registró el reverso n.º 31/)).toBeInTheDocument();
    expect(bodies(spy, "/settlements/operations/30/reverse")[0]).toEqual({
      client_request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
      reason: "Cargado dos veces",
      business_date: todayIso(),
    });
    expect(await screen.findByText("Esta operación fue revertida y ya no afecta los saldos.")).toBeInTheDocument();
  });

  it("keeps an uncertain reversal for that operation only", async () => {
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("admin")),
      "GET /settlements/operations/30": () => jsonResponse(200, OPERATION),
      "POST /settlements/operations/30/reverse": (_init, call) => {
        if (call === 1) throw new TypeError("Failed to fetch");
        return jsonResponse(200, { ...OPERATION, id: 31, type: "REVERSAL", reverses_operation_id: 30 });
      },
    });
    renderWithQuery(<OperationDetail operationId={30} />);
    await userEvent.click(await screen.findByRole("button", { name: "Revertir operación" }));
    let dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Duplicada");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir operación" }));
    await within(dialog).findByRole("alert");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    expect(sessionStorage.getItem("districom:settlements:pending:reverse:30")).not.toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Revertir operación" }));
    dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Reverso de la operación n.º 30");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reintentar la misma operación" }));
    expect(await screen.findByText(/Se registró el reverso n.º 31/)).toBeInTheDocument();
    const sent = bodies(spy, "/settlements/operations/30/reverse");
    expect(sent[1]).toEqual(sent[0]);
    expect(sessionStorage.length).toBe(0);
  });

  it("explains an already reversed operation", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("admin")),
      "GET /settlements/operations/30": () => jsonResponse(200, OPERATION),
      "POST /settlements/operations/30/reverse": () => apiError(409, "operation_already_reversed"),
    });
    renderWithQuery(<OperationDetail operationId={30} />);
    await userEvent.click(await screen.findByRole("button", { name: "Revertir operación" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "x");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir operación" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Esta operación ya fue revertida.");
  });

  it("says when the operation does not exist", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("viewer")),
      "GET /settlements/operations/99": () => apiError(404, "operation_not_found"),
    });
    renderWithQuery(<OperationDetail operationId={99} />);
    expect(await screen.findByText("La operación no existe")).toBeInTheDocument();
  });
});
