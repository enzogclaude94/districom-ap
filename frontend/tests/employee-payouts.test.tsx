import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CurrentUser } from "@/lib/api/auth";
import { OPERATION_LABELS, sourceHref, sourceLabel } from "@/modules/settlements/cash/labels";
import { documentOf, ORIGIN_LABELS, PAYMENT_STATUS_LABELS } from "@/modules/settlements/employees/labels";
import { PayrollView } from "@/modules/settlements/employees/payroll-detail";

import { jsonResponse, renderWithQuery } from "./render";

vi.mock("next/navigation", () => ({
  usePathname: () => "/rendiciones/empleados/liquidaciones/3",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
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

function mockApi(routes: Record<string, (init: RequestInit | undefined) => Response>) {
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
  applications: [],
};

const PAYOUT = {
  id: 21,
  payroll_id: 3,
  payroll_closing_id: 5,
  payroll_version: 1,
  payroll_period: "18/09/2026 al 18/10/2026",
  employee_id: 7,
  employee_name: "Pedro Gómez",
  business_date: "2026-10-18",
  amount: "50000.00",
  reference: null,
  items: [{ method: "TRANSFER", account_id: 2, account_name: "Banco Galicia", amount: "50000.00" }],
  operation_id: 80,
  in_effect: false,
  reversal_operation_id: 81,
  reversed_at: "2026-10-18T15:00:00Z",
  reversed_by_name: "Enzo",
  reversal_reason: "Transferencia rechazada",
  created_at: "2026-10-18T14:00:00Z",
  created_by_name: "Cajera",
};

const PAYROLL = {
  id: 3,
  employee_id: 7,
  employee_name: "Pedro Gómez",
  kind: "REGULAR",
  period_start: "2026-09-18",
  period_end: "2026-10-18",
  payment_date: null,
  status: "CLOSED",
  version: 1,
  totals: { ...totals, deduction_pct: "23.53" },
  totals_from_draft: false,
  in_correction: false,
  notes: null,
  created_at: "2026-10-18T12:00:00Z",
  created_by_name: "Enzo",
  cancelled_at: null,
  cancelled_by_name: null,
  cancel_reason: null,
  salary_hint: { status: "MANUAL", message: "", amount: null },
  lines: [],
  legs: [],
  installments: [],
  preview: null,
  closing,
  closings: [closing],
  reopenings: [],
  payment_status: "UNPAID",
  payment: { net: "650000.00", paid: "0.00", pending: "650000.00", status: "UNPAID" },
  payouts: [PAYOUT],
};

const ACCOUNTS = [
  { id: 1, code: "main_cash", name: "Caja principal", kind: "CASH_BOX", active: true, balance: "500000.00" },
  { id: 2, code: "bank", name: "Banco Galicia", kind: "BANK", active: true, balance: "300000.00" },
  { id: 3, code: "cheque_portfolio", name: "Cartera", kind: "CHEQUE_PORTFOLIO", active: true, balance: "0.00" },
].map((a) => ({ ...a, allow_negative: false, overdrawn: false, overdrawn_label: null }));

const PREVIEW = {
  net: "650000.00",
  paid: "0.00",
  amount: "300000.00",
  pending_after: "350000.00",
  balance_before: "-400000.00",
  balance_after: "-100000.00",
  legs: [
    { account_id: 1, account_name: "Caja principal", account_kind: "CASH_BOX", amount: "-100000.00" },
    { account_id: 2, account_name: "Banco Galicia", account_kind: "BANK", amount: "-200000.00" },
    { account_id: 10, account_name: "Cuenta interna · Pedro Gómez", account_kind: "EMPLOYEE", amount: "300000.00" },
  ],
};

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe("payouts of internal payrolls (R8-5)", () => {
  it("shows net, paid and pending and pays with cash and a transfer after the backend's preview", async () => {
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("operator")),
      [`GET ${EF}/payrolls/3`]: () => jsonResponse(200, PAYROLL),
      "GET /settlements/accounts": () => jsonResponse(200, ACCOUNTS),
      [`POST ${EF}/payrolls/3/payouts/preview`]: () => jsonResponse(200, PREVIEW),
      [`POST ${EF}/payrolls/3/payouts`]: () => jsonResponse(201, { ...PAYOUT, id: 22, in_effect: true }),
    });
    renderWithQuery(<PayrollView payrollId={3} />);
    const payment = await screen.findByTestId("payroll-payment");
    expect(payment).toHaveTextContent("Neto$ 650.000,00");
    expect(payment).toHaveTextContent("Pagado$ 0,00");
    expect(payment).toHaveTextContent("Pendiente de pago$ 650.000,00");
    expect(screen.getByTestId("payment-status")).toHaveTextContent("Sin pagar");
    // The reversed payout stays as history, with who and why; an operator cannot reverse it.
    const history = screen.getByTestId("payout-21");
    expect(history).toHaveTextContent("Revertido por Enzo");
    expect(history).toHaveTextContent("Transferencia rechazada");
    expect(screen.queryByRole("button", { name: "Revertir pago" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Registrar pago" }));
    const dialog = screen.getByRole("dialog");
    const first = within(dialog).getByRole("textbox", { name: "Importe del medio 1" });
    expect(first).toHaveValue("650000,00"); // the whole pending by default
    // Cash only from cash boxes: neither banks nor cheques are offered.
    const box = await within(dialog).findByRole("combobox", { name: "Caja del medio 1" });
    await waitFor(() => expect(within(box).getAllByRole("option")).toHaveLength(2));
    expect(within(box).queryByText(/Cartera/)).not.toBeInTheDocument();
    await userEvent.selectOptions(box, "1");
    await userEvent.clear(first);
    await userEvent.type(first, "100.000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar medio" }));
    await userEvent.selectOptions(within(dialog).getByRole("combobox", { name: "Banco del medio 2" }), "2");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Importe del medio 2" }), "200.000");
    expect(within(dialog).getByTestId("payout-total")).toHaveTextContent("Total del pago: $ 300.000,00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));

    const preview = await screen.findByTestId("payout-preview");
    expect(preview).toHaveTextContent("Liquidación$ 650.000,00");
    expect(preview).toHaveTextContent("Ya pagado$ 0,00");
    expect(preview).toHaveTextContent("Este pago$ 300.000,00");
    expect(preview).toHaveTextContent("Quedará pendiente$ 350.000,00");
    const legs = screen.getByTestId("payout-legs");
    expect(legs).toHaveTextContent("Caja principal-$ 100.000,00");
    expect(legs).toHaveTextContent("Banco Galicia-$ 200.000,00");
    expect(legs).toHaveTextContent("Cuenta interna · Pedro Gómez+$ 300.000,00");
    await userEvent.click(screen.getByRole("button", { name: "Confirmar pago" }));
    await waitFor(() => expect(sent(spy, "POST", "/payrolls/3/payouts")).toHaveLength(1));
    const body = sent(spy, "POST", "/payrolls/3/payouts")[0];
    expect(body).toMatchObject({
      payroll_closing_id: 5,
      items: [
        { method: "CASH", account_id: 1, amount: "100000.00" },
        { method: "TRANSFER", account_id: 2, amount: "200000.00" },
      ],
      reference: null,
    });
    expect(typeof body.client_request_id).toBe("string");
  });

  it("never offers more than the pending nor a date before the period's end", async () => {
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("operator")),
      [`GET ${EF}/payrolls/3`]: () => jsonResponse(200, PAYROLL),
      "GET /settlements/accounts": () => jsonResponse(200, ACCOUNTS),
    });
    renderWithQuery(<PayrollView payrollId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Registrar pago" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.selectOptions(await within(dialog).findByRole("combobox", { name: "Caja del medio 1" }), "1");
    const amount = within(dialog).getByRole("textbox", { name: "Importe del medio 1" });
    await userEvent.clear(amount);
    await userEvent.type(amount, "650.000,01");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("supera lo pendiente");
    await userEvent.clear(amount);
    await userEvent.type(amount, "1.000");
    const date = within(dialog).getByLabelText("Fecha del pago");
    await userEvent.clear(date);
    await userEvent.type(date, "2026-10-17");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("es un adelanto");
    expect(sent(spy, "POST", "/payrolls/3/payouts/preview")).toEqual([]);
  });

  it("only pays with the cash role too, and only an admin of the area reverses", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("operator", "viewer")),
      [`GET ${EF}/payrolls/3`]: () => jsonResponse(200, PAYROLL),
    });
    const { unmount } = renderWithQuery(<PayrollView payrollId={3} />);
    expect(await screen.findByTestId("payroll-payment")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Registrar pago" })).not.toBeInTheDocument();
    unmount();
    vi.restoreAllMocks();
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("admin")),
      [`GET ${EF}/payrolls/3`]: () =>
        jsonResponse(200, {
          ...PAYROLL,
          payment_status: "PAID",
          payment: { net: "650000.00", paid: "650000.00", pending: "0.00", status: "PAID" },
          payouts: [{ ...PAYOUT, id: 22, amount: "650000.00", in_effect: true, reversed_at: null }],
        }),
    });
    renderWithQuery(<PayrollView payrollId={3} />);
    expect(await screen.findByTestId("payment-status")).toHaveTextContent("Pagada");
    expect(screen.queryByRole("button", { name: "Registrar pago" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revertir pago" })).toBeInTheDocument();
    expect(screen.getByText(/no se reabre: revertí los pagos/)).toBeInTheDocument();
  });

  it("names payouts in the internal account and in Cajas", () => {
    expect(ORIGIN_LABELS.PAYOUT).toBe("Pago de liquidación interna");
    expect(ORIGIN_LABELS.PAYOUT_REVERSAL).toBe("Reverso de pago de liquidación");
    expect(PAYMENT_STATUS_LABELS).toEqual({
      UNPAID: "Sin pagar",
      PARTIALLY_PAID: "Pagada parcialmente",
      PAID: "Pagada",
    });
    expect(
      documentOf({
        entry_id: 1,
        operation_id: 2,
        business_date: "2026-10-18",
        amount: "300000.00",
        reading: "CHARGE",
        origin: "PAYOUT",
        document_kind: "EMPLOYEE_PAYOUT",
        document_id: 21,
        document_date: "2026-10-18",
        document_number: null,
        created_at: "2026-10-18T12:00:00Z",
      }),
    ).toEqual({ label: "Pago de liquidación n.º 21", href: "/rendiciones/empleados/pagos/21" });
    expect(OPERATION_LABELS.EMPLOYEE_PAYOUT).toBe("Pago de liquidación interna");
    expect(OPERATION_LABELS.EMPLOYEE_RESERVED).toBe("Movimiento de empleado (reservado)");
    expect(sourceLabel("employee_payout", 21)).toBe("Pago de liquidación interna #21");
    expect(sourceHref("employee_payout", 21)).toBe("/rendiciones/empleados/pagos/21");
  });
});
