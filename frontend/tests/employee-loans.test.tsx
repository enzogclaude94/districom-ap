import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CurrentUser } from "@/lib/api/auth";
import { todayIso } from "@/lib/format";
import { OPERATION_LABELS, sourceHref, sourceLabel } from "@/modules/settlements/cash/labels";
import { EmployeeFinanceDetailView } from "@/modules/settlements/employees/employee-detail";
import { INSTALLMENT_STATUS_LABELS, ORIGIN_LABELS, documentOf } from "@/modules/settlements/employees/labels";
import { EmployeeLoanView } from "@/modules/settlements/employees/loan-detail";
import { addMonths, sumAmounts } from "@/modules/settlements/employees/loan-schedule";

import { jsonResponse, renderWithQuery } from "./render";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => "/rendiciones/empleados/7",
  useRouter: () => ({ replace: vi.fn(), push }),
}));

const AREA = "settlements.employee_finance";
type Role = "viewer" | "operator" | "admin";
const EF = "/settlements/employee-finance";

const me = (area: Role, settlements: Role = "operator"): CurrentUser => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "settlements", name: "Rendiciones", role: settlements }],
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

const LEGS = [
  { account_id: 1, account_name: "Caja principal", account_kind: "CASH_BOX", amount: "-300000.00" },
  { account_id: 9, account_name: "Cuenta interna · Pedro Gómez", account_kind: "EMPLOYEE", amount: "300000.00" },
];

const installment = (number: number, due_on: string, amount: string, status = "SCHEDULED", extra = {}) => ({
  id: 100 + number,
  number,
  due_on,
  amount,
  status,
  deferred_from_id: null,
  deferred_from_number: null,
  deferred_reason: null,
  deferred_at: null,
  deferred_by_name: null,
  replaced_by: [],
  created_at: "2026-11-02T12:00:00Z",
  ...extra,
});

const LOAN = {
  id: 4,
  business_date: "2026-11-02",
  principal: "300000.00",
  installment_count: 3,
  status: "ACTIVE",
  balance: "300000.00",
  scheduled_count: 3,
  next_due_on: "2026-11-30",
  description: "Moto",
  employee_id: 7,
  employee_name: "Pedro Gómez",
  funding_account_id: 1,
  funding_account_name: "Caja principal",
  first_due_on: "2026-11-30",
  reference: null,
  operation_id: 50,
  reversal_operation_id: null,
  reversed_at: null,
  reversed_by_name: null,
  reversal_reason: null,
  created_at: "2026-11-02T12:00:00Z",
  created_by_name: "Enzo",
  legs: LEGS,
  installments: [
    installment(1, "2026-11-30", "100000.00"),
    installment(2, "2026-12-30", "100000.00"),
    installment(3, "2027-01-30", "100000.00"),
  ],
};

function detailRoutes(area: Role, extra: Record<string, Handler> = {}, settlements: Role = "operator") {
  return {
    "GET /auth/me": () => jsonResponse(200, me(area, settlements)),
    [`GET ${EF}/employees/7`]: () =>
      jsonResponse(200, {
        employee_id: 7,
        full_name: "Pedro Gómez",
        active: true,
        balance: "300000.00",
        balance_reading: "OWES_COMPANY",
        current_term: null,
        next_term: null,
        hire_date: null,
        has_account: true,
      }),
    [`GET ${EF}/employees/7/movements`]: () =>
      jsonResponse(200, {
        items: [
          {
            entry_id: 41,
            operation_id: 50,
            business_date: "2026-11-02",
            amount: "300000.00",
            reading: "CHARGE",
            origin: "LOAN",
            document_kind: "EMPLOYEE_LOAN",
            document_id: 4,
            document_date: "2026-11-02",
            document_number: null,
            description: "Moto",
            document_reversed: false,
            created_at: "2026-11-02T12:00:00Z",
          },
        ],
        total: 1,
      }),
    [`GET ${EF}/employees/7/compensation-terms`]: () => jsonResponse(200, []),
    [`GET ${EF}/employees/7/loans`]: () => jsonResponse(200, [LOAN]),
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
      ]),
    ...extra,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  push.mockReset();
  window.sessionStorage.clear();
});

describe("labels and helpers of R8-3", () => {
  it("names the loan in the internal account and in Cajas", () => {
    expect(ORIGIN_LABELS.LOAN).toBe("Préstamo");
    expect(ORIGIN_LABELS.LOAN_REVERSAL).toBe("Reverso de préstamo");
    expect(OPERATION_LABELS.EMPLOYEE_LOAN).toBe("Préstamo a empleado");
    expect(sourceLabel("employee_loan", 4)).toBe("Préstamo a empleado #4");
    expect(sourceHref("employee_loan", 4)).toBe("/rendiciones/empleados/prestamos/4");
  });

  it("an installment is scheduled, deferred or cancelled: never paid nor applied", () => {
    expect(Object.values(INSTALLMENT_STATUS_LABELS)).toEqual(["Programada", "Postergada", "Cancelada"]);
  });

  it("adds months keeping the day or the month's last day, and sums typed amounts in cents", () => {
    expect(addMonths("2026-11-02", 1)).toBe("2026-12-02");
    expect(addMonths("2027-01-31", 1)).toBe("2027-02-28");
    expect(addMonths("2026-12-15", 2)).toBe("2027-02-15");
    expect(sumAmounts(["33.333,33", "33333,33", "33333.34"])).toBe("100000.00");
    expect(sumAmounts(["40.000", "60.000"])).toBe("100000.00");
    expect(sumAmounts(["40.000", "x"])).toBeNull();
  });
});

describe("loans on the employee's page", () => {
  it("the loan reads as one movement with a link to it, and is listed with its balance", async () => {
    mockApi(detailRoutes("viewer"));
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    const row = await screen.findByTestId("account-movement-41");
    expect(within(row).getByText("Préstamo")).toBeInTheDocument();
    expect(within(row).getByText("Le suma deuda")).toBeInTheDocument();
    expect(within(row).getByText("+$ 300.000,00")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Ver préstamo n.º 4" })).toHaveAttribute(
      "href",
      "/rendiciones/empleados/prestamos/4",
    );
    const loan = await screen.findByTestId("loan-row-4");
    expect(within(loan).getByRole("link", { name: "Préstamo n.º 4" })).toBeInTheDocument();
    expect(loan).toHaveTextContent("$ 300.000,00");
    expect(loan).toHaveTextContent("Vigente");
    expect(screen.queryByRole("button", { name: "Dar préstamo" })).not.toBeInTheDocument();
  });

  it("only an area admin who is also operator in Rendiciones gives a loan", async () => {
    mockApi(detailRoutes("admin", {}, "viewer"));
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    await screen.findByTestId("loan-row-4");
    expect(screen.queryByRole("button", { name: "Dar préstamo" })).not.toBeInTheDocument();
    expect(documentOf({ document_kind: "EMPLOYEE_LOAN", document_id: 4, document_number: null } as never).label).toBe(
      "Ver préstamo n.º 4",
    );
  });

  it("gives a loan in two steps: terms, then the backend's schedule, editable before confirming", async () => {
    const spy = mockApi(
      detailRoutes("admin", {
        [`POST ${EF}/employees/7/loans/preview`]: () =>
          jsonResponse(200, {
            principal: "300000.00",
            business_date: todayIso(),
            funding_account_id: 1,
            funding_account_name: "Caja principal",
            balance_before: "0.00",
            balance_after: "300000.00",
            legs: LEGS,
            installments: [
              { number: 1, due_on: addMonths(todayIso(), 1), amount: "100000.00" },
              { number: 2, due_on: addMonths(todayIso(), 2), amount: "100000.00" },
              { number: 3, due_on: addMonths(todayIso(), 3), amount: "100000.00" },
            ],
            total: "300000.00",
          }),
        [`POST ${EF}/employees/7/loans`]: () => jsonResponse(201, LOAN),
      }),
    );
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Dar préstamo" }));
    let dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/Sin intereses/)).toBeInTheDocument();
    // The first installment defaults to a month after the loan and may be moved to the loan's own month.
    expect(within(dialog).getByLabelText("Vence la primera cuota")).toHaveValue(addMonths(todayIso(), 1));
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    expect(within(dialog).getByText(/Ingresá el importe del préstamo/)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Importe del préstamo"), "300.000");
    const select = within(dialog).getByLabelText("Se entrega desde");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(2));
    await userEvent.selectOptions(select, "1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    dialog = screen.getByRole("dialog");
    const confirmation = await within(dialog).findByTestId("loan-confirmation");
    await within(confirmation).findByTestId("loan-legs");
    expect(confirmation).toHaveTextContent(
      "Se entregan $ 300.000,00 desde Caja principal: Pedro Gómez deberá $ 300.000,00 más a la empresa, en 3 cuotas sin interés.",
    );
    expect(within(confirmation).getByTestId("loan-legs")).toHaveTextContent("Caja principal-$ 300.000,00");
    expect(within(confirmation).getByTestId("loan-legs")).toHaveTextContent(
      "Cuenta interna · Pedro Gómez+$ 300.000,00",
    );
    expect(posted(spy, "/loans/preview")[0]).toMatchObject({
      principal: "300000.00",
      funding_account_id: 1,
      installment_count: 3,
      first_due_on: addMonths(todayIso(), 1),
    });
    // Editing an amount so the total no longer fits blocks the confirmation.
    const second = within(confirmation).getByLabelText("Importe de la cuota 2");
    await userEvent.clear(second);
    await userEvent.type(second, "50.000");
    expect(within(confirmation).getByTestId("loan-totals")).toHaveTextContent("$ 250.000,00");
    expect(within(dialog).getByRole("button", { name: "Confirmar y dar préstamo" })).toBeDisabled();
    const third = within(confirmation).getByLabelText("Importe de la cuota 3");
    await userEvent.clear(third);
    await userEvent.type(third, "150.000");
    expect(within(confirmation).getByTestId("loan-totals")).toHaveTextContent("Nuevo cronograma$ 300.000,00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar y dar préstamo" }));
    await waitFor(() => expect(posted(spy, "/employees/7/loans")).toHaveLength(1));
    const [body] = posted(spy, "/employees/7/loans");
    expect(body).toMatchObject({
      principal: "300000.00",
      funding_account_id: 1,
      installment_count: 3,
      installments: [
        { due_on: addMonths(todayIso(), 1), amount: "100000.00" },
        { due_on: addMonths(todayIso(), 2), amount: "50000.00" },
        { due_on: addMonths(todayIso(), 3), amount: "150000.00" },
      ],
      description: null,
    });
    expect(body.client_request_id).toEqual(expect.any(String));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/rendiciones/empleados/prestamos/4"));
  });
});

describe("EmployeeLoanView", () => {
  it("shows the schedule and defers an installment into several, with the totals side by side", async () => {
    const deferred = {
      ...LOAN,
      installments: [
        installment(1, "2026-11-30", "100000.00"),
        installment(2, "2026-12-30", "100000.00", "DEFERRED", {
          deferred_reason: "Vacaciones",
          deferred_at: "2026-11-25T12:00:00Z",
          deferred_by_name: "Admin",
          replaced_by: [
            { number: 4, due_on: "2026-12-31", amount: "40000.00" },
            { number: 5, due_on: "2027-01-31", amount: "60000.00" },
          ],
        }),
        installment(3, "2027-01-30", "100000.00"),
        installment(4, "2026-12-31", "40000.00", "SCHEDULED", { deferred_from_id: 102, deferred_from_number: 2 }),
        installment(5, "2027-01-31", "60000.00", "SCHEDULED", { deferred_from_id: 102, deferred_from_number: 2 }),
      ],
    };
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("admin", "viewer")),
      [`GET ${EF}/loans/4`]: () => jsonResponse(200, LOAN),
      [`POST ${EF}/loan-installments/102/defer`]: () => jsonResponse(200, deferred),
    });
    renderWithQuery(<EmployeeLoanView loanId={4} />);
    expect(await screen.findByRole("heading", { name: "Préstamo n.º 4" })).toBeInTheDocument();
    expect(screen.getByTestId("loan-status")).toHaveTextContent("Vigente");
    expect(screen.getByTestId("loan-facts")).toHaveTextContent("Saldo del préstamo$ 300.000,00");
    expect(screen.getByTestId("installment-1")).toHaveTextContent("Programada");
    // Deferring moves no money: the area admin does it without the cash role; reversing needs it.
    expect(screen.queryByRole("button", { name: "Revertir" })).not.toBeInTheDocument();
    await userEvent.click(within(screen.getByTestId("installment-2")).getByRole("button", { name: "Postergar" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByTestId("deferral-totals")).toHaveTextContent("Importe reemplazado$ 100.000,00");
    expect(within(dialog).getByTestId("deferral-totals")).toHaveTextContent("Nuevo cronograma$ 100.000,00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar cuota" }));
    const first = within(dialog).getByLabelText("Importe de la cuota 4");
    await userEvent.clear(first);
    await userEvent.type(first, "40.000");
    await userEvent.type(within(dialog).getByLabelText("Importe de la cuota 5"), "70.000");
    expect(within(dialog).getByTestId("deferral-totals")).toHaveTextContent("Nuevo cronograma$ 110.000,00");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Vacaciones");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar postergación" }));
    expect(within(dialog).getByText(/tienen que sumar exactamente \$ 100\.000,00/)).toBeInTheDocument();
    expect(posted(spy, "/defer")).toHaveLength(0);
    const second = within(dialog).getByLabelText("Importe de la cuota 5");
    await userEvent.clear(second);
    await userEvent.type(second, "60.000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar postergación" }));
    await waitFor(() => expect(posted(spy, "/loan-installments/102/defer")).toHaveLength(1));
    expect(posted(spy, "/defer")[0]).toMatchObject({
      reason: "Vacaciones",
      replacements: [
        { due_on: "2027-01-30", amount: "40000.00" },
        { due_on: "2027-02-28", amount: "60000.00" },
      ],
    });
  });

  it("keeps the deferred installment in the history with what replaced it", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("viewer")),
      [`GET ${EF}/loans/4`]: () =>
        jsonResponse(200, {
          ...LOAN,
          installments: [
            installment(1, "2026-11-30", "100000.00"),
            installment(2, "2026-11-30", "100000.00", "DEFERRED", {
              deferred_reason: "Pidió más plazo",
              deferred_at: "2026-11-25T12:00:00Z",
              deferred_by_name: "Admin",
              replaced_by: [{ number: 4, due_on: "2026-12-31", amount: "100000.00" }],
            }),
            installment(3, "2027-01-30", "100000.00"),
            installment(4, "2026-12-31", "100000.00", "SCHEDULED", { deferred_from_id: 102, deferred_from_number: 2 }),
          ],
        }),
    });
    renderWithQuery(<EmployeeLoanView loanId={4} />);
    const history = await screen.findByTestId("deferral-2");
    expect(history).toHaveTextContent("Cuota 2 · $ 100.000,00 · vencía 30/11/2026");
    expect(history).toHaveTextContent("por Admin");
    expect(history).toHaveTextContent("Motivo: Pidió más plazo");
    expect(history).toHaveTextContent("Reemplazada por: Cuota 4 · $ 100.000,00 · 31/12/2026");
    expect(screen.getByTestId("installment-2")).toHaveTextContent("Postergada");
    expect(screen.getByTestId("installment-4")).toHaveTextContent("reemplaza a la 2");
    expect(screen.queryByRole("button", { name: "Postergar" })).not.toBeInTheDocument();
  });

  it("an admin with the cash role reverses the whole loan with a reason", async () => {
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("admin")),
      [`GET ${EF}/loans/4`]: () => jsonResponse(200, LOAN),
      [`POST ${EF}/loans/4/reverse`]: () =>
        jsonResponse(200, {
          ...LOAN,
          status: "REVERSED",
          balance: "0.00",
          reversal_operation_id: 51,
          reversed_at: "2026-11-03T12:00:00Z",
          reversed_by_name: "Enzo",
          reversal_reason: "Error",
          installments: LOAN.installments.map((i) => ({ ...i, status: "CANCELLED" })),
        }),
    });
    renderWithQuery(<EmployeeLoanView loanId={4} />);
    await userEvent.click(await screen.findByRole("button", { name: "Revertir" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Las cuotas programadas se cancelan; las postergadas quedan como historia.");
    expect(within(dialog).getByTestId("loan-reversal-legs")).toHaveTextContent("Caja principal+$ 300.000,00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar reverso" }));
    expect(within(dialog).getByText("Indicá el motivo del reverso.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Motivo del reverso"), "Error");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar reverso" }));
    await waitFor(() => expect(posted(spy, "/loans/4/reverse")).toHaveLength(1));
    expect(posted(spy, "/loans/4/reverse")[0]).toMatchObject({ reason: "Error" });
  });
});
