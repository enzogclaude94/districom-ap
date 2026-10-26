import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { AREA, mainCash, noSideScroll, settlement, setUp } from "./employee-finance-setup";
import { api, day } from "./settlement-setup";

/**
 * R8-5 end to end (Enzo's points 17–20), on desktop and mobile: paying closed internal payrolls. The setup and the
 * payroll go by API (R8-4 covers them on screen); every payment and reversal goes through the screen.
 */

const EF = "/settlements/employee-finance";

interface Account {
  id: number;
  code: string | null;
  balance: string;
}

const cents = (amount: string) => Math.round(Number(amount) * 100);

async function account(request: APIRequestContext, code: string) {
  const all = await api<Account[]>(request, "GET", "/settlements/accounts");
  return all.find((a) => a.code === code)!;
}

const balance = async (request: APIRequestContext, code: string) => cents((await account(request, code)).balance);

interface Payroll {
  id: number;
  version: number;
  preview: { preview_token: string } | null;
  legs: { entry_id: number }[];
  payouts: { id: number; operation_id: number }[];
}

/** A payroll from 30 days ago to today with the given credits, closed as the screen closes it. */
async function closedPayroll(
  request: APIRequestContext,
  employeeId: number,
  lines: [string, string][],
  kind: "REGULAR" | "EXTRAORDINARY" = "REGULAR",
  include: (p: Payroll) => number[] = () => [],
) {
  const created = await api<Payroll>(request, "POST", `${EF}/payrolls`, {
    client_request_id: crypto.randomUUID(),
    employee_id: employeeId,
    kind,
    period_start: day(-30),
    period_end: day(0),
    payment_date: null,
  });
  for (const [lineKind, amount] of lines) {
    await api(request, "POST", `${EF}/payrolls/${created.id}/lines`, {
      kind: lineKind,
      amount,
      description: lineKind === "SALARY" ? "Sin condición salarial cargada" : null,
    });
  }
  for (const entry of include(created)) {
    await api(request, "PUT", `${EF}/payrolls/${created.id}/choices`, { ledger_entry_id: entry, included: true });
  }
  const current = await api<Payroll>(request, "GET", `${EF}/payrolls/${created.id}`);
  await api(request, "POST", `${EF}/payrolls/${created.id}/close`, {
    version: current.version,
    preview_token: current.preview!.preview_token,
    confirm: true,
  });
  return created.id;
}

/** Net, paid and pending as the «Pago» block shows them. */
async function expectPayment(page: Page, status: string, [net, paid, pending]: string[]) {
  await expect(page.getByTestId("payment-status")).toHaveText(status);
  const block = page.getByTestId("payroll-payment");
  await expect(block).toContainText(`Neto${net}`);
  await expect(block).toContainText(`Pagado${paid}`);
  await expect(block).toContainText(`Pendiente de pago${pending}`);
}

type Mean = { method: "CASH" | "TRANSFER"; accountId: number; amount?: string };

/** «Registrar pago»: fills the means (the first one comes with the whole pending), reviews and checks the preview. */
async function pay(page: Page, means: Mean[], preview: string[]) {
  await page.getByRole("button", { name: "Registrar pago" }).click();
  const dialog = page.getByRole("dialog");
  for (const [index, m] of means.entries()) {
    const n = index + 1;
    if (index > 0) await dialog.getByRole("button", { name: "Agregar medio" }).click();
    await dialog.getByLabel(`Medio ${n}`, { exact: true }).selectOption(m.method);
    await dialog
      .getByLabel(`${m.method === "CASH" ? "Caja" : "Banco"} del medio ${n}`)
      .selectOption(String(m.accountId));
    if (m.amount) await dialog.getByLabel(`Importe del medio ${n}`).fill(m.amount);
  }
  await dialog.getByRole("button", { name: "Revisar" }).click();
  const facts = dialog.getByTestId("payout-preview");
  const [net, paid, amount, pending] = preview;
  await expect(facts).toContainText(`Liquidación${net}`);
  await expect(facts).toContainText(`Ya pagado${paid}`);
  await expect(facts).toContainText(`Este pago${amount}`);
  await expect(facts).toContainText(`Quedará pendiente${pending}`);
  await expect(dialog.getByTestId("payout-legs")).toBeVisible();
  await noSideScroll(page);
  await dialog.getByRole("button", { name: "Confirmar pago" }).click();
  await expect(dialog).toBeHidden();
}

test("case 1 continued: $650.000 paid in two payments, mixed cash and transfer, with exact balances", async ({
  page,
}) => {
  const { employeeId } = await setUp(page, "p1");
  const { request } = page;
  await settlement(request, employeeId, 1000000, 1000000); // cash for the advance, the loan and the payments
  await settlement(request, employeeId, 100000, 70000); // a $30.000 shortage
  const box = await mainCash(request);
  const bank = (await account(request, "bank")).id;
  await api(request, "POST", "/settlements/operations/bank-deposit", {
    client_request_id: crypto.randomUUID(),
    business_date: day(0),
    cash_account_id: box,
    bank_account_id: bank,
    amount: "200000",
  });
  const movement = { business_date: day(0), reference: null };
  await api(request, "POST", `${EF}/employees/${employeeId}/consumptions`, {
    ...movement,
    client_request_id: crypto.randomUUID(),
    amount: "20000",
    description: "Mercadería",
  });
  await api(request, "POST", `${EF}/employees/${employeeId}/advances`, {
    ...movement,
    client_request_id: crypto.randomUUID(),
    amount: "100000",
    description: "Adelanto de sueldo",
    funding_account_id: box,
  });
  const loan = await api<{ id: number }>(request, "POST", `${EF}/employees/${employeeId}/loans`, {
    client_request_id: crypto.randomUUID(),
    business_date: day(0),
    principal: "300000",
    funding_account_id: box,
    installment_count: 6,
    first_due_on: day(0),
  });
  // Salary 800k + bonus 50k − shortage 30k − consumption 20k − advance 100k − installment 50k = 650k (R8-4 case 1).
  const payrollId = await closedPayroll(request, employeeId, [
    ["SALARY", "800000"],
    ["BONUS", "50000"],
  ]);

  await page.goto(`/rendiciones/empleados/liquidaciones/${payrollId}`);
  await expectPayment(page, "Sin pagar", ["$ 650.000,00", "$ 0,00", "$ 650.000,00"]);
  await noSideScroll(page);

  // Payment 1: $100.000 cash + $200.000 transfer.
  const before = { cash: await balance(request, "main_cash"), bank: await balance(request, "bank") };
  await pay(
    page,
    [
      { method: "CASH", accountId: box, amount: "100.000" },
      { method: "TRANSFER", accountId: bank, amount: "200.000" },
    ],
    ["$ 650.000,00", "$ 0,00", "$ 300.000,00", "$ 350.000,00"],
  );
  await expectPayment(page, "Pagada parcialmente", ["$ 650.000,00", "$ 300.000,00", "$ 350.000,00"]);
  expect(await balance(request, "main_cash")).toBe(before.cash - 10_000_000);
  expect(await balance(request, "bank")).toBe(before.bank - 20_000_000);
  await expect(page.getByTestId("payout-history")).toContainText("Transferencia");
  await page.goto(`/rendiciones/empleados/${employeeId}`);
  await expect(page.getByTestId("internal-balance")).toHaveText("-$ 100.000,00");
  await expect(page.getByTestId("pay-summary")).toContainText("Liquidaciones pendientes de pago$ 350.000,00 (1)");

  // Payment 2: the rest, $350.000 cash (the default amount).
  await page.goto(`/rendiciones/empleados/liquidaciones/${payrollId}`);
  await pay(page, [{ method: "CASH", accountId: box }], ["$ 650.000,00", "$ 300.000,00", "$ 350.000,00", "$ 0,00"]);
  await expectPayment(page, "Pagada", ["$ 650.000,00", "$ 650.000,00", "$ 0,00"]);
  await expect(page.getByRole("button", { name: "Registrar pago" })).toHaveCount(0);
  expect(await balance(request, "main_cash")).toBe(before.cash - 45_000_000);
  expect(await balance(request, "bank")).toBe(before.bank - 20_000_000);
  await noSideScroll(page);

  // The account: −400k +300k +350k = +250k, the employee owes the loan; the loan itself is untouched.
  await page.goto(`/rendiciones/empleados/${employeeId}`);
  await expect(page.getByTestId("internal-balance")).toHaveText("$ 250.000,00");
  const summary = page.getByTestId("pay-summary");
  await expect(summary).toContainText("Debe a la empresa $ 250.000,00");
  await expect(summary).toContainText("Liquidaciones pendientes de pago$ 0,00");
  await expect(summary).toContainText("Pagada");
  await noSideScroll(page);
  await page.goto(`/rendiciones/empleados/prestamos/${loan.id}`);
  await expect(page.getByTestId("loan-facts")).toContainText("Saldo del préstamo$ 250.000,00");

  // Cajas, with the area: «Pago de liquidación interna».
  const payroll = await api<Payroll>(request, "GET", `${EF}/payrolls/${payrollId}`);
  await page.goto(`/rendiciones/cajas/operaciones/${payroll.payouts[0].operation_id}`);
  await expect(page.getByText("Pago de liquidación interna").first()).toBeVisible();

  // R8 navigation: «Pagos de liquidaciones» lists both.
  await page.goto("/rendiciones/empleados/pagos");
  await expect(page.getByRole("heading", { name: "Pagos de liquidaciones" })).toBeVisible();
  for (const p of payroll.payouts) await expect(page.getByTestId(`payout-row-${p.id}`)).toBeVisible();
  await noSideScroll(page);
});

test("NO_ACCRUAL: a $20.000 surplus applied by an extraordinary payroll is paid and the account goes to zero", async ({
  page,
}) => {
  const { email, employeeId, name } = await setUp(page, "p2");
  const { request } = page;
  await settlement(request, employeeId, 100000, 120000); // a $20.000 surplus: −20k on the account
  const box = await mainCash(request);
  const payrollId = await closedPayroll(request, employeeId, [], "EXTRAORDINARY", (p) => p.legs.map((l) => l.entry_id));
  const closing = await api<{ closing: { kind: string } }>(request, "GET", `${EF}/payrolls/${payrollId}`);
  expect(closing.closing.kind).toBe("NO_ACCRUAL");

  await page.goto(`/rendiciones/empleados/liquidaciones/${payrollId}`);
  await expectPayment(page, "Sin pagar", ["$ 20.000,00", "$ 0,00", "$ 20.000,00"]);
  const before = await balance(request, "main_cash");
  await pay(page, [{ method: "CASH", accountId: box }], ["$ 20.000,00", "$ 0,00", "$ 20.000,00", "$ 0,00"]);
  await expectPayment(page, "Pagada", ["$ 20.000,00", "$ 20.000,00", "$ 0,00"]);
  expect(await balance(request, "main_cash")).toBe(before - 2_000_000);
  await page.goto(`/rendiciones/empleados/${employeeId}`);
  await expect(page.getByTestId("internal-balance")).toHaveText("$ 0,00");

  // Without the area the payout is only a reserved movement of the cash box.
  const payroll = await api<Payroll>(request, "GET", `${EF}/payrolls/${payrollId}`);
  await api(request, "POST", "/auth/access-grants/revoke", { email, key: AREA });
  await page.goto(`/rendiciones/cajas/operaciones/${payroll.payouts[0].operation_id}`);
  await expect(page.getByText("Movimiento de empleado (reservado)").first()).toBeVisible();
  await expect(page.getByText(name)).toHaveCount(0);
  const hidden = await request.get(`/api/v1${EF}/payouts/${payroll.payouts[0].id}`);
  expect(hidden.status()).toBe(404);
  await noSideScroll(page);
});

test("reopen: blocked while paid; the payout is reversed, the payroll reopens and the history stays", async ({
  page,
}) => {
  const { employeeId } = await setUp(page, "p3");
  const { request } = page;
  await settlement(request, employeeId, 200000, 200000);
  const box = await mainCash(request);
  const payrollId = await closedPayroll(request, employeeId, [["SALARY", "100000"]]);
  const before = await balance(request, "main_cash");

  await page.goto(`/rendiciones/empleados/liquidaciones/${payrollId}`);
  await pay(page, [{ method: "CASH", accountId: box }], ["$ 100.000,00", "$ 0,00", "$ 100.000,00", "$ 0,00"]);
  await expectPayment(page, "Pagada", ["$ 100.000,00", "$ 100.000,00", "$ 0,00"]);
  expect(await balance(request, "main_cash")).toBe(before - 10_000_000);

  // Reopening is refused with Enzo's message.
  await page.getByRole("button", { name: "Reabrir" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("Motivo").fill("Faltó un bono");
  await dialog.getByRole("button", { name: "Reabrir" }).click();
  await expect(dialog).toContainText("Esta liquidación tiene pagos vigentes. Revertí los pagos antes de reabrirla");
  await dialog.getByRole("button", { name: "Cancelar" }).click();
  await expect(page.getByTestId("payroll-status")).toHaveText("Cerrada");

  // The payout is reversed whole: the cash comes back.
  await page.getByRole("button", { name: "Revertir pago" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Motivo del reverso").fill("Se pagó antes de cargar el bono");
  await dialog.getByRole("button", { name: "Confirmar reverso" }).click();
  await expect(dialog).toBeHidden();
  await expectPayment(page, "Sin pagar", ["$ 100.000,00", "$ 0,00", "$ 100.000,00"]);
  expect(await balance(request, "main_cash")).toBe(before);
  const history = page.getByTestId("payout-history");
  await expect(history).toContainText("Revertido");
  await expect(history).toContainText("Se pagó antes de cargar el bono");

  // Now it reopens, and the reversed payout is still in its history.
  await page.getByRole("button", { name: "Reabrir" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Motivo").fill("Faltó un bono");
  await dialog.getByRole("button", { name: "Reabrir" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("payroll-status")).toHaveText("Abierta");
  await expect(page.getByTestId("payout-history")).toContainText("Revertido");
  await noSideScroll(page);
});
