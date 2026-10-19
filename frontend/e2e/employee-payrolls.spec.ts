import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { mainCash, noSideScroll, settlement, setUp } from "./employee-finance-setup";
import { api, day } from "./settlement-setup";

/**
 * R8-4 end to end (Enzo's point 20), on desktop and mobile: the four cases of the internal payroll. The setup (the
 * settlements, the movements and the loan) goes by API; the payroll itself is built, reviewed and closed on screen.
 * A fresh platform admin, created with the CLI, holds the area only for these tests.
 */

const EF = "/settlements/employee-finance";

/** Opens a payroll from the employee's page: the period runs from 30 days ago to today. */
async function newPayroll(page: Page, employeeId: number, kind: "Regular" | "Extraordinaria" = "Regular") {
  await page.goto(`/rendiciones/empleados/${employeeId}`);
  await page.getByRole("button", { name: "Nueva liquidación" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Tipo").selectOption({ label: (await optionText(dialog.getByLabel("Tipo"), kind))! });
  await dialog.getByLabel("Desde").fill(day(-30));
  await dialog.getByLabel("Hasta (inclusive)").fill(day(0));
  await dialog.getByRole("button", { name: "Crear liquidación" }).click();
  await expect(page.getByRole("heading", { name: /^Liquidación interna n\.º \d+$/ })).toBeVisible();
  await expect(page.getByTestId("payroll-status")).toHaveText("Abierta");
  return Number(page.url().split("/").pop());
}

async function optionText(select: ReturnType<Page["getByLabel"]>, startsWith: string) {
  return select.locator("option", { hasText: startsWith }).first().textContent();
}

async function addLine(page: Page, concept: string, amount: string, note?: string) {
  await page.getByRole("button", { name: "Agregar haber" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Concepto").selectOption({ label: concept });
  await dialog.getByLabel("Importe").fill(amount);
  if (note) await dialog.getByLabel(/^Nota/).fill(note);
  await dialog.getByRole("button", { name: "Guardar haber" }).click();
  await expect(dialog).toBeHidden();
}

async function closePayroll(page: Page) {
  await page.getByRole("button", { name: "Cerrar liquidación" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("payroll-status")).toHaveText("Cerrada");
}

/** Haberes, descuentos, créditos aplicados and neto as the screen shows them. */
async function expectTotals(page: Page, testId: string, [credits, deductions, applied, net]: string[]) {
  const totals = page.getByTestId(testId);
  await expect(totals).toContainText(`Haberes${credits}`);
  await expect(totals).toContainText(`Descuentos${deductions}`);
  await expect(totals).toContainText(`Créditos aplicados${applied}`);
  await expect(totals).toContainText(`Neto${net}`);
}

interface Payroll {
  legs: unknown[];
  installments: unknown[];
  closing: { kind: string; operation_id: number | null } | null;
}

/** A REGULAR payroll of tomorrow only, by API: what the next one would propose. Cancelled right after. */
async function nextProposal(request: APIRequestContext, employeeId: number) {
  const next = await api<Payroll & { id: number }>(request, "POST", `${EF}/payrolls`, {
    client_request_id: crypto.randomUUID(),
    employee_id: employeeId,
    kind: "REGULAR",
    period_start: day(1),
    period_end: day(1),
    payment_date: null,
  });
  await api(request, "POST", `${EF}/payrolls/${next.id}/cancel`, { reason: "Solo para ver la propuesta" });
  return next;
}

test("case 1: salary, bonus, shortage, consumption, advance and an installment close to a net of $650.000", async ({
  page,
}) => {
  const { employeeId } = await setUp(page, "c1");
  await settlement(page.request, employeeId, 500000, 500000); // cash for the advance and the loan
  await settlement(page.request, employeeId, 100000, 70000); // a $30.000 shortage
  const box = await mainCash(page.request);
  const movement = { business_date: day(0), reference: null };
  await api(page.request, "POST", `${EF}/employees/${employeeId}/consumptions`, {
    ...movement,
    client_request_id: crypto.randomUUID(),
    amount: "20000",
    description: "Mercadería",
  });
  await api(page.request, "POST", `${EF}/employees/${employeeId}/advances`, {
    ...movement,
    client_request_id: crypto.randomUUID(),
    amount: "100000",
    description: "Adelanto de sueldo",
    funding_account_id: box,
  });
  const loan = await api<{ id: number }>(page.request, "POST", `${EF}/employees/${employeeId}/loans`, {
    client_request_id: crypto.randomUUID(),
    business_date: day(0),
    principal: "300000",
    funding_account_id: box,
    installment_count: 6,
    first_due_on: day(0),
  });

  await newPayroll(page, employeeId);
  // No salary term for the period: a warning, and the salary is entered by hand with its note.
  await expect(page.getByTestId("salary-hint")).not.toBeEmpty();
  await addLine(page, "Sueldo", "800.000", "Sin condición salarial cargada");
  await addLine(page, "Bono", "50.000");
  await expect(page.getByTestId("payroll-lines")).toContainText("$ 800.000,00");
  await expect(page.getByTestId("payroll-lines")).toContainText("$ 50.000,00");
  // Proposed in full: the shortage, the consumption and the advance; the loan only through its installment.
  const legs = page.locator('[data-testid^="payroll-leg-"]');
  await expect(legs).toHaveCount(3);
  await expect(legs.filter({ hasText: "Faltante de rendición" })).toContainText("$ 30.000,00");
  await expect(legs.filter({ hasText: "Consumo" })).toContainText("$ 20.000,00");
  await expect(legs.filter({ hasText: "Adelanto" })).toContainText("$ 100.000,00");
  const installments = page.getByTestId("payroll-installments");
  await expect(installments).toContainText("Cuota 1");
  await expect(installments).toContainText("$ 50.000,00");
  await expect(installments.getByRole("checkbox")).toBeChecked();
  await expectTotals(page, "payroll-preview", ["$ 850.000,00", "$ 200.000,00", "$ 0,00", "$ 650.000,00"]);
  await noSideScroll(page);

  await closePayroll(page);
  await expectTotals(page, "payroll-closing", ["$ 850.000,00", "$ 200.000,00", "$ 0,00", "$ 650.000,00"]);
  await expect(page.getByTestId("closing-v1")).toContainText("con devengamiento");
  await expect(page.getByTestId("closing-v1")).toContainText("Cuota 1 del préstamo");
  await noSideScroll(page);

  // The loan: installment 1 applied, $250.000 pending.
  await page.goto(`/rendiciones/empleados/prestamos/${loan.id}`);
  await expect(page.getByTestId("installment-1")).toContainText("Aplicada");
  await expect(page.getByTestId("installment-2")).toContainText("Pendiente de aplicar");
  await expect(page.getByTestId("loan-facts")).toContainText("Saldo del préstamo$ 250.000,00");
  await expect(page.getByTestId("installment-1").getByRole("button", { name: "Postergar" })).toHaveCount(0);

  // The internal account: +30k +20k +100k +300k −850k = −400k, the company owes the employee.
  await page.goto(`/rendiciones/empleados/${employeeId}`);
  await expect(page.getByTestId("internal-balance")).toHaveText("-$ 400.000,00");
  await expect(page.getByText("Liquidación interna (haberes)")).toBeVisible();
  // Nothing applied comes back.
  const next = await nextProposal(page.request, employeeId);
  expect(next.legs).toEqual([]);
  expect(next.installments).toEqual([]);
});

test("case 2: a surplus is a credit applied once, never duplicated", async ({ page }) => {
  const { employeeId } = await setUp(page, "c2");
  await settlement(page.request, employeeId, 100000, 120000); // a $20.000 surplus, in favour of the employee

  await newPayroll(page, employeeId);
  await addLine(page, "Sueldo", "800.000", "Sin condición salarial cargada");
  await addLine(page, "Bono", "30.000");
  const legs = page.locator('[data-testid^="payroll-leg-"]');
  await expect(legs).toHaveCount(1);
  await expect(legs.first()).toContainText("Sobrante de rendición");
  await expect(legs.first()).toContainText("-$ 20.000,00");
  await expectTotals(page, "payroll-preview", ["$ 830.000,00", "$ 0,00", "$ 20.000,00", "$ 850.000,00"]);
  await noSideScroll(page);
  await closePayroll(page);
  await expectTotals(page, "payroll-closing", ["$ 830.000,00", "$ 0,00", "$ 20.000,00", "$ 850.000,00"]);

  // −20k (surplus) −830k (the close): the surplus counted once on the account, and never proposed again.
  await page.goto(`/rendiciones/empleados/${employeeId}`);
  await expect(page.getByTestId("internal-balance")).toHaveText("-$ 850.000,00");
  expect((await nextProposal(page.request, employeeId)).legs).toEqual([]);
});

test("case 3: an original and its reversal neutralize exactly and are not proposed again", async ({ page }) => {
  const { employeeId } = await setUp(page, "c3");
  const settlementId = await settlement(page.request, employeeId, 100000, 70000); // $30.000 short…
  // …then corrected: reopened and counted in full, so the shortage is reversed.
  await api(page.request, "POST", `/settlements/deliveries/${settlementId}/reopen`, {
    version: 1,
    reason: "Se contó mal: no faltaba nada",
  });
  await api(page.request, "PUT", `/settlements/deliveries/${settlementId}/count`, {
    lines: [{ denomination_code: "ARS_10000", quantity: 10, destination_account_id: await mainCash(page.request) }],
  });
  const preview = await api<{ preview_token: string }>(
    page.request,
    "GET",
    `/settlements/deliveries/${settlementId}/closing-preview`,
  );
  await api(page.request, "POST", `/settlements/deliveries/${settlementId}/close`, {
    version: 2,
    preview_token: preview.preview_token,
    allocations: [],
    confirm: true,
  });

  await newPayroll(page, employeeId);
  await addLine(page, "Sueldo", "800.000", "Sin condición salarial cargada");
  const legs = page.locator('[data-testid^="payroll-leg-"]');
  await expect(legs).toHaveCount(2);
  await expect(legs.filter({ hasText: "Faltante de rendición" })).toContainText("Revertido después");
  await expect(legs.filter({ hasText: "Corrección de rendición (reverso)" })).toContainText("-$ 30.000,00");
  await expect(page.getByTestId("neutralized")).toContainText("efecto neto $ 0,00 (2 aplicaciones)");
  await expectTotals(page, "payroll-preview", ["$ 800.000,00", "$ 30.000,00", "$ 30.000,00", "$ 800.000,00"]);
  await noSideScroll(page);
  await closePayroll(page);
  // Two applications, never one netted.
  const closing = page.getByTestId("closing-v1");
  await expect(closing).toContainText("Faltante de rendición");
  await expect(closing).toContainText("Corrección de rendición (reverso)");
  expect((await nextProposal(page.request, employeeId)).legs).toEqual([]);
});

test("case 4: with no credits the close applies an existing credit without PAYROLL_CLOSE", async ({ page }) => {
  const { employeeId } = await setUp(page, "c4");
  await settlement(page.request, employeeId, 100000, 120000); // a $20.000 surplus

  const payrollId = await newPayroll(page, employeeId, "Extraordinaria");
  await expect(page.getByText("Cuotas", { exact: true })).toHaveCount(0); // extraordinary: never installments
  const leg = page.locator('[data-testid^="payroll-leg-"]').first();
  await expect(leg).toContainText("Sobrante de rendición");
  const include = leg.getByRole("checkbox");
  await expect(include).not.toBeChecked(); // extraordinary: listed, not included
  await include.click(); // the server decides: the box follows the saved draft
  await expect(include).toBeChecked();
  await expectTotals(page, "payroll-preview", ["$ 0,00", "$ 0,00", "$ 20.000,00", "$ 20.000,00"]);
  await expect(page.getByText("Sin haberes: el cierre no publica asiento.")).toBeVisible();
  await noSideScroll(page);
  await closePayroll(page);
  await expect(page.getByTestId("closing-v1")).toContainText("sin devengamiento");

  const payroll = await api<Payroll>(page.request, "GET", `${EF}/payrolls/${payrollId}`);
  expect(payroll.closing).toMatchObject({ kind: "NO_ACCRUAL", operation_id: null });
  // Nothing new on the account: only the surplus.
  await page.goto(`/rendiciones/empleados/${employeeId}`);
  await expect(page.getByTestId("internal-balance")).toHaveText("-$ 20.000,00");
  await expect(page.getByText("Liquidación interna (haberes)")).toHaveCount(0);
});
