import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CurrentUser } from "@/lib/api/auth";
import { documentOf, ORIGIN_LABELS } from "@/modules/settlements/employees/labels";
import { PayrollView } from "@/modules/settlements/employees/payroll-detail";
import { PayrollList } from "@/modules/settlements/employees/payroll-list";

import { jsonResponse, renderWithQuery } from "./render";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => "/rendiciones/empleados/liquidaciones/3",
  useRouter: () => ({ replace: vi.fn(), push }),
}));

const AREA = "settlements.employee_finance";
type Role = "viewer" | "operator" | "admin";
const EF = "/settlements/employee-finance";

const me = (area: Role): CurrentUser => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "settlements", name: "Rendiciones", role: "operator" }],
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

const sent = (spy: ReturnType<typeof mockApi>, method: string, path: string) =>
  spy.mock.calls
    .filter(([input, init]) => init?.method === method && String(input).endsWith(path))
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

const totals = { credits: "850000.00", deductions: "200000.00", credits_applied: "0.00", net: "650000.00" };

const leg = (entry: number, origin: string, amount: string, extra = {}) => ({
  entry_id: entry,
  operation_id: entry + 100,
  origin,
  business_date: "2026-10-13",
  document_kind: null,
  document_id: null,
  description: null,
  group_operation_id: entry + 100,
  reversed: false,
  original_amount: amount,
  applied_elsewhere: "0.00",
  available: amount,
  proposed: true,
  included: true,
  amount,
  partial: false,
  ...extra,
});

const PAYROLL = {
  id: 3,
  employee_id: 7,
  employee_name: "Pedro Gómez",
  kind: "REGULAR",
  period_start: "2026-09-18",
  period_end: "2026-10-18",
  payment_date: null,
  status: "OPEN",
  version: 1,
  totals: { ...totals, deduction_pct: "23.53" },
  totals_from_draft: true,
  in_correction: false,
  notes: null,
  created_at: "2026-10-18T12:00:00Z",
  created_by_name: "Enzo",
  cancelled_at: null,
  cancelled_by_name: null,
  cancel_reason: null,
  salary_hint: {
    status: "MANUAL",
    message: "El período no es un mes calendario completo: no hay prorrateo automático.",
    amount: null,
  },
  lines: [
    {
      id: 1,
      kind: "SALARY",
      amount: "800000.00",
      description: "Período parcial",
      source: "MANUAL",
      compensation_term_id: null,
    },
    { id: 2, kind: "BONUS", amount: "50000.00", description: null, source: "MANUAL", compensation_term_id: null },
  ],
  legs: [
    leg(11, "SETTLEMENT_SHORTAGE", "30000.00"),
    leg(12, "CONSUMPTION", "20000.00"),
    leg(13, "ADVANCE", "100000.00"),
    leg(14, "SETTLEMENT_SHORTAGE", "5000.00", { group_operation_id: 900, reversed: true }),
    leg(15, "SETTLEMENT_CORRECTION", "-5000.00", { group_operation_id: 900 }),
  ],
  installments: [
    {
      installment_id: 101,
      loan_id: 4,
      loan_description: null,
      number: 1,
      due_on: "2026-10-08",
      amount: "50000.00",
      overdue: false,
      included: true,
    },
  ],
  preview: { ...totals, deduction_pct: "23.53", preview_token: "a".repeat(64), blockers: [], accrual: "FINANCIAL" },
  closing: null,
  closings: [],
  reopenings: [],
  payment_status: null,
  payment: null,
  payouts: [],
};

afterEach(() => vi.restoreAllMocks());

describe("internal payroll (R8-4)", () => {
  it("shows the draft, its preview from the backend and closes with the token it previewed", async () => {
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("admin")),
      [`GET ${EF}/payrolls/3`]: () => jsonResponse(200, PAYROLL),
      [`PUT ${EF}/payrolls/3/choices`]: () => jsonResponse(200, PAYROLL),
      [`POST ${EF}/payrolls/3/close`]: () =>
        jsonResponse(200, { ...PAYROLL, status: "CLOSED", preview: null, totals_from_draft: false }),
    });
    renderWithQuery(<PayrollView payrollId={3} />);
    expect(await screen.findByRole("heading", { name: "Liquidación interna n.º 3" })).toBeInTheDocument();
    expect(screen.getByText(/no es el recibo de sueldo legal/)).toBeInTheDocument();
    expect(screen.getByTestId("salary-hint")).toHaveTextContent("no hay prorrateo automático");
    const preview = screen.getByTestId("payroll-preview");
    expect(preview).toHaveTextContent("Haberes$ 850.000,00");
    expect(preview).toHaveTextContent("Descuentos$ 200.000,00");
    expect(preview).toHaveTextContent("Neto$ 650.000,00");
    expect(preview).toHaveTextContent("23,53 %");
    // An original and its reversal, both included, read as one correction with no net effect.
    expect(screen.getByTestId("neutralized")).toHaveTextContent("Corrección de rendición · efecto neto $ 0,00");
    // An installment is whole: a checkbox, never an amount field.
    const installments = screen.getByTestId("payroll-installments");
    expect(within(installments).queryByRole("textbox")).not.toBeInTheDocument();
    await userEvent.click(within(installments).getByRole("checkbox"));
    await waitFor(() =>
      expect(sent(spy, "PUT", "/payrolls/3/choices")).toEqual([{ installment_id: 101, included: false }]),
    );
    // A partial amount of a movement.
    const consumption = screen.getByTestId("payroll-leg-12");
    const amount = within(consumption).getByRole("textbox");
    await userEvent.clear(amount);
    await userEvent.type(amount, "12.000");
    await userEvent.click(within(consumption).getByRole("button", { name: "Aplicar" }));
    await waitFor(() =>
      expect(sent(spy, "PUT", "/payrolls/3/choices")[1]).toEqual({
        ledger_entry_id: 12,
        included: true,
        amount: "12000.00",
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Cerrar liquidación" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("«Sueldos y bonos internos» +$ 850.000,00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar cierre" }));
    await waitFor(() =>
      expect(sent(spy, "POST", "/payrolls/3/close")).toEqual([
        { version: 1, preview_token: "a".repeat(64), confirm: true },
      ]),
    );
  });

  it("an extraordinary payroll has no installments; a blocker stops the close", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("admin")),
      [`GET ${EF}/payrolls/3`]: () =>
        jsonResponse(200, {
          ...PAYROLL,
          kind: "EXTRAORDINARY",
          installments: [],
          preview: {
            ...PAYROLL.preview,
            net: "-40000.00",
            blockers: [{ code: "net_negative", message: "El neto daría $-40000.00: excluí movimientos." }],
          },
        }),
    });
    renderWithQuery(<PayrollView payrollId={3} />);
    expect(await screen.findByText("Movimientos a aplicar")).toBeInTheDocument();
    expect(screen.queryByText("Cuotas")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("El neto daría");
    expect(screen.getByRole("button", { name: "Cerrar liquidación" })).toBeDisabled();
  });

  it("a viewer reads, never edits; a closed payroll shows its close and history", async () => {
    const closing = {
      ...totals,
      deduction_pct: "23.53",
      id: 5,
      version: 1,
      kind: "FINANCIAL",
      operation_id: 70,
      reversal_operation_id: null,
      closed_at: "2026-10-18T13:00:00Z",
      closed_by_name: "Enzo",
      superseded_at: null,
      lines: [],
      applications: [
        {
          entry_id: 99,
          origin: "LOAN",
          business_date: "2026-09-28",
          amount: "50000.00",
          installment_id: 101,
          installment_number: 1,
          loan_id: 4,
        },
      ],
    };
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("viewer")),
      [`GET ${EF}/payrolls/3`]: () =>
        jsonResponse(200, { ...PAYROLL, status: "CLOSED", preview: null, closing, closings: [closing] }),
    });
    renderWithQuery(<PayrollView payrollId={3} />);
    expect(await screen.findByTestId("payroll-closing")).toHaveTextContent("Neto$ 650.000,00");
    expect(screen.getByTestId("closing-v1")).toHaveTextContent("Cuota 1 del préstamo n.º 4: +$ 50.000,00");
    expect(screen.queryByRole("button", { name: "Reabrir" })).not.toBeInTheDocument();
    expect(screen.queryByText("Movimientos a aplicar")).not.toBeInTheDocument();
  });

  it("lists payrolls with their totals", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("operator")),
      [`GET ${EF}/payrolls`]: () => jsonResponse(200, { items: [PAYROLL], total: 1 }),
    });
    renderWithQuery(<PayrollList />);
    const row = await screen.findByTestId("payroll-row-3");
    expect(row).toHaveTextContent("Pedro Gómez");
    expect(row).toHaveTextContent("$ 650.000,00");
    expect(row).toHaveTextContent("Importes del borrador");
    expect(screen.getByRole("button", { name: "Nueva liquidación" })).toBeInTheDocument();
  });

  it("names payroll movements in the internal account", () => {
    expect(ORIGIN_LABELS.PAYROLL).toBe("Liquidación interna (haberes)");
    expect(
      documentOf({
        entry_id: 1,
        operation_id: 2,
        business_date: "2026-10-18",
        amount: "-1.00",
        reading: "CREDIT",
        origin: "PAYROLL",
        document_kind: "EMPLOYEE_PAYROLL",
        document_id: 3,
        document_date: "2026-10-18",
        document_number: null,
        created_at: "2026-10-18T12:00:00Z",
      }),
    ).toEqual({ label: "Liquidación interna n.º 3", href: "/rendiciones/empleados/liquidaciones/3" });
  });
});
