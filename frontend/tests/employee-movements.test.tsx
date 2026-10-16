import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CurrentUser } from "@/lib/api/auth";
import { todayIso } from "@/lib/format";
import { OPERATION_LABELS, sourceHref, sourceLabel } from "@/modules/settlements/cash/labels";
import { EmployeeFinanceDetailView } from "@/modules/settlements/employees/employee-detail";
import { ORIGIN_LABELS, adjustmentEffect } from "@/modules/settlements/employees/labels";
import { EmployeeMovementView } from "@/modules/settlements/employees/movement-detail";

import { jsonResponse, renderWithQuery } from "./render";

vi.mock("next/navigation", () => ({
  usePathname: () => "/rendiciones/empleados/7",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const AREA = "settlements.employee_finance";
type Role = "viewer" | "operator" | "admin";
const EF = "/settlements/employee-finance";

const me = (area: Role): CurrentUser => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "settlements", name: "Rendiciones", role: "viewer" }],
  areas: [{ key: AREA, name: "Empleados", role: area }],
});

type Handler = (init: RequestInit | undefined) => Response;

function mockApi(routes: Record<string, Handler>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const handler = routes[`${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`];
    if (!handler)
      return jsonResponse(404, { code: "not_found", message: "No encontrado.", details: null, request_id: null });
    return handler(init);
  });
}

const posted = (spy: ReturnType<typeof mockApi>, path: string) =>
  spy.mock.calls
    .filter(([input, init]) => init?.method === "POST" && String(input).endsWith(path))
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

const MOVEMENT_ROW = {
  entry_id: 41,
  operation_id: 20,
  business_date: "2026-10-12",
  amount: "20000.00",
  reading: "CHARGE",
  origin: "CONSUMPTION",
  document_kind: "EMPLOYEE_MOVEMENT",
  document_id: 3,
  document_date: "2026-10-12",
  document_number: null,
  description: "Gaseosas",
  document_reversed: true,
  created_at: "2026-10-12T12:00:00Z",
};

function routes(area: Role, extra: Record<string, Handler> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, me(area)),
    [`GET ${EF}/employees/7`]: () =>
      jsonResponse(200, {
        employee_id: 7,
        full_name: "Pedro Gómez",
        active: true,
        balance: "20000.00",
        balance_reading: "OWES_COMPANY",
        current_term: null,
        next_term: null,
        hire_date: null,
        has_account: true,
      }),
    [`GET ${EF}/employees/7/movements`]: () => jsonResponse(200, { items: [MOVEMENT_ROW], total: 1 }),
    [`GET ${EF}/employees/7/compensation-terms`]: () => jsonResponse(200, []),
    "GET /settlements/accounts": () =>
      jsonResponse(200, [
        {
          id: 1,
          code: "main_cash",
          name: "Caja principal",
          kind: "CASH_BOX",
          allow_negative: false,
          active: true,
          balance: "500000.00",
          overdrawn: false,
          overdrawn_label: null,
        },
        {
          id: 2,
          code: null,
          name: "Caja vieja",
          kind: "CASH_BOX",
          allow_negative: false,
          active: false,
          balance: "0.00",
          overdrawn: false,
          overdrawn_label: null,
        },
        {
          id: 5,
          code: null,
          name: "Aportes",
          kind: "OWNER_EQUITY",
          allow_negative: true,
          active: true,
          balance: "0.00",
          overdrawn: false,
          overdrawn_label: null,
        },
      ]),
    ...extra,
  };
}

const MOVEMENT = {
  id: 3,
  kind: "ADVANCE",
  direction: null,
  employee_id: 7,
  employee_name: "Pedro Gómez",
  business_date: "2026-10-12",
  amount: "100000.00",
  employee_amount: "100000.00",
  funding_account_id: 1,
  funding_account_name: "Caja principal",
  description: "Adelanto quincena",
  reference: "Recibo 12",
  status: "IN_EFFECT",
  operation_id: 20,
  reversal_operation_id: null,
  reversed_at: null,
  reversed_by_name: null,
  reversal_reason: null,
  created_at: "2026-10-12T12:00:00Z",
  created_by_name: "Enzo",
  legs: [
    { account_id: 1, account_name: "Caja principal", account_kind: "CASH_BOX", amount: "-100000.00" },
    { account_id: 9, account_name: "Cuenta interna · Pedro Gómez", account_kind: "EMPLOYEE", amount: "100000.00" },
  ],
};

beforeEach(() => window.sessionStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("labels of R8-2", () => {
  it("names each origin without «deducción» and each adjustment by its effect", () => {
    expect(ORIGIN_LABELS.CONSUMPTION).toBe("Consumo");
    expect(ORIGIN_LABELS.ADVANCE).toBe("Adelanto");
    expect(ORIGIN_LABELS.ADJUSTMENT_CHARGE).toBe("Ajuste en contra");
    expect(ORIGIN_LABELS.ADJUSTMENT_CREDIT).toBe("Ajuste a favor");
    expect(ORIGIN_LABELS.ADVANCE_REVERSAL).toBe("Reverso de adelanto");
    expect(Object.values(ORIGIN_LABELS).join(" ")).not.toMatch(/deducci/i);
    expect(adjustmentEffect("CHARGE", "5000.00")).toBe("El empleado deberá $ 5.000,00 más a la empresa.");
    expect(adjustmentEffect("CREDIT", "15000.00")).toBe("La empresa reconocerá $ 15.000,00 a favor del empleado.");
  });

  it("Cajas reads the new operations and links to the movement", () => {
    expect(OPERATION_LABELS.EMPLOYEE_ADVANCE).toBe("Adelanto a empleado");
    expect(OPERATION_LABELS.EMPLOYEE_RESERVED).toBe("Movimiento de empleado (reservado)");
    expect(sourceLabel("employee_movement", 3)).toBe("Movimiento de empleado #3");
    expect(sourceHref("employee_movement", 3)).toBe("/rendiciones/empleados/movimientos/3");
  });
});

describe("actions on the internal account", () => {
  it("a viewer of the area registers nothing", async () => {
    mockApi(routes("viewer"));
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    await screen.findByTestId("account-movement-41");
    expect(screen.queryByRole("button", { name: "Registrar consumo" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Entregar adelanto" })).not.toBeInTheDocument();
  });

  it("an operator registers consumptions only", async () => {
    mockApi(routes("operator"));
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    expect(await screen.findByRole("button", { name: "Registrar consumo" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Entregar adelanto" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Registrar ajuste" })).not.toBeInTheDocument();
  });

  it("lists the movement with its description, document link and reversed mark", async () => {
    mockApi(routes("viewer"));
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    const row = await screen.findByTestId("account-movement-41");
    expect(within(row).getByText("Consumo")).toBeInTheDocument();
    expect(within(row).getByText("Gaseosas")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Movimiento n.º 3" })).toHaveAttribute(
      "href",
      "/rendiciones/empleados/movimientos/3",
    );
    expect(within(row).getByText("Revertido")).toBeInTheDocument();
    expect(within(row).getByText("+$ 20.000,00")).toBeInTheDocument();
  });

  it("a consumption is confirmed with the backend's preview before it is registered", async () => {
    const spy = mockApi(
      routes("operator", {
        [`POST ${EF}/employees/7/movements/preview`]: () =>
          jsonResponse(200, {
            kind: "CONSUMPTION",
            direction: null,
            amount: "20000.00",
            employee_amount: "20000.00",
            balance_before: "20000.00",
            balance_after: "40000.00",
            legs: [
              {
                account_id: 9,
                account_name: "Cuenta interna · Pedro Gómez",
                account_kind: "EMPLOYEE",
                amount: "20000.00",
              },
              {
                account_id: 30,
                account_name: "Consumos de empleados",
                account_kind: "EMPLOYEE_CONSUMPTIONS",
                amount: "-20000.00",
              },
            ],
          }),
        [`POST ${EF}/employees/7/consumptions`]: () => jsonResponse(201, { ...MOVEMENT, kind: "CONSUMPTION" }),
      }),
    );
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Registrar consumo" }));
    let dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    expect(within(dialog).getByText(/Ingresá un importe/)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Importe"), "20.000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    expect(within(dialog).getByText("Indicá una descripción.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Descripción"), "Gaseosas");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    dialog = screen.getByRole("dialog");
    const confirmation = await within(dialog).findByTestId("movement-confirmation");
    expect(within(confirmation).getByText("El empleado deberá $ 20.000,00 más a la empresa.")).toBeInTheDocument();
    expect(within(confirmation).getByTestId("balance-after")).toHaveTextContent("$ 40.000,00 · Debe a la empresa");
    const legs = within(confirmation).getByTestId("movement-legs");
    expect(legs).toHaveTextContent("Cuenta interna · Pedro Gómez+$ 20.000,00");
    expect(legs).toHaveTextContent("Consumos de empleados-$ 20.000,00");
    expect(posted(spy, "/consumptions")).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar y registrar" }));
    await waitFor(() => expect(posted(spy, "/consumptions")).toHaveLength(1));
    const [body] = posted(spy, "/consumptions");
    expect(body).toMatchObject({
      business_date: todayIso(),
      amount: "20000.00",
      description: "Gaseosas",
      reference: null,
    });
    expect(body.client_request_id).toEqual(expect.any(String));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("an advance is drawn from an active cash box or bank and shows the funds error", async () => {
    const spy = mockApi(
      routes("admin", {
        [`POST ${EF}/employees/7/movements/preview`]: () =>
          jsonResponse(409, {
            code: "insufficient_funds",
            message: "Caja principal quedaría con saldo negativo.",
            details: { account_id: 1, business_date: todayIso(), balance: "500000.00", amount: "-900000.00" },
            request_id: "r",
          }),
      }),
    );
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Entregar adelanto" }));
    const dialog = screen.getByRole("dialog");
    const select = within(dialog).getByLabelText("Se entrega desde");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(2));
    expect(within(select).getByRole("option", { name: /Caja principal/ })).toBeInTheDocument();
    await userEvent.selectOptions(select, "1");
    await userEvent.type(within(dialog).getByLabelText("Importe"), "900.000");
    await userEvent.type(within(dialog).getByLabelText("Descripción"), "Adelanto");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    expect(await screen.findByText(/No hay saldo suficiente en Caja principal/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirmar y registrar" })).toBeDisabled();
    expect(posted(spy, "/movements/preview")[0]).toMatchObject({ kind: "ADVANCE", funding_account_id: 1 });
  });

  it("an adjustment explains it is exceptional and says its effect before naming accounts", async () => {
    const spy = mockApi(
      routes("admin", {
        [`POST ${EF}/employees/7/movements/preview`]: () =>
          jsonResponse(200, {
            kind: "ADJUSTMENT",
            direction: "CREDIT",
            amount: "15000.00",
            employee_amount: "-15000.00",
            balance_before: "20000.00",
            balance_after: "5000.00",
            legs: [
              {
                account_id: 9,
                account_name: "Cuenta interna · Pedro Gómez",
                account_kind: "EMPLOYEE",
                amount: "-15000.00",
              },
              {
                account_id: 31,
                account_name: "Ajustes a empleados",
                account_kind: "EMPLOYEE_ADJUSTMENTS",
                amount: "15000.00",
              },
            ],
          }),
        [`POST ${EF}/employees/7/adjustments`]: () => jsonResponse(201, { ...MOVEMENT, kind: "ADJUSTMENT" }),
      }),
    );
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Registrar ajuste" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/excepcional y correctivo/)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Importe"), "15000");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Reconocimiento");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    expect(within(dialog).getByText(/Elegí si el ajuste es en contra o a favor/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("radio", { name: /A favor del empleado/ }));
    expect(within(dialog).getByTestId("adjustment-effect")).toHaveTextContent(
      "La empresa reconocerá $ 15.000,00 a favor del empleado.",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    const confirmation = await screen.findByTestId("movement-confirmation");
    expect(confirmation).toHaveTextContent("La empresa reconocerá $ 15.000,00 a favor del empleado.");
    expect(within(confirmation).getByTestId("movement-legs")).toHaveTextContent("Ajustes a empleados+$ 15.000,00");
    await userEvent.click(screen.getByRole("button", { name: "Confirmar y registrar" }));
    await waitFor(() => expect(posted(spy, "/adjustments")).toHaveLength(1));
    expect(posted(spy, "/adjustments")[0]).toMatchObject({ direction: "CREDIT", description: "Reconocimiento" });
  });
});

describe("EmployeeMovementView", () => {
  it("shows the movement and lets the area admin reverse it with a reason", async () => {
    const reversed = {
      ...MOVEMENT,
      status: "REVERSED",
      reversal_operation_id: 21,
      reversed_at: "2026-10-13T12:00:00Z",
      reversed_by_name: "Enzo",
      reversal_reason: "Error de carga",
    };
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("admin")),
      [`GET ${EF}/employee-movements/3`]: () => jsonResponse(200, MOVEMENT),
      [`POST ${EF}/employee-movements/3/reverse`]: () => jsonResponse(200, reversed),
    });
    renderWithQuery(<EmployeeMovementView movementId={3} />);
    expect(await screen.findByRole("heading", { name: "Adelanto n.º 3" })).toBeInTheDocument();
    expect(screen.getByTestId("movement-status")).toHaveTextContent("Vigente");
    expect(screen.getByTestId("movement-detail-legs")).toHaveTextContent("Caja principal-$ 100.000,00");
    await userEvent.click(screen.getByRole("button", { name: "Revertir" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByTestId("reversal-legs")).toHaveTextContent("Caja principal+$ 100.000,00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar reverso" }));
    expect(within(dialog).getByText("Indicá el motivo del reverso.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Motivo del reverso"), "Error de carga");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar reverso" }));
    await waitFor(() => expect(posted(spy, "/employee-movements/3/reverse")).toHaveLength(1));
    expect(posted(spy, "/employee-movements/3/reverse")[0]).toMatchObject({ reason: "Error de carga" });
  });

  it("a viewer of the area cannot reverse", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("operator")),
      [`GET ${EF}/employee-movements/3`]: () => jsonResponse(200, MOVEMENT),
    });
    renderWithQuery(<EmployeeMovementView movementId={3} />);
    await screen.findByRole("heading", { name: "Adelanto n.º 3" });
    expect(screen.queryByRole("button", { name: "Revertir" })).not.toBeInTheDocument();
  });
});
