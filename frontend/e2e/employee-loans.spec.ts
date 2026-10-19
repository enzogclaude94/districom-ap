import { execFileSync } from "node:child_process";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { login } from "./helpers";
import { api, driver, openSettlement } from "./settlement-setup";

/**
 * R8-3 end to end (Enzo's point 18): give a loan → the cash box and the internal account → the schedule → defer an
 * installment into two → defer one of those again (chain and history) → reverse another loan without applications →
 * without the area, the loan is only a reserved movement of the cash box. A fresh platform admin, created with the
 * CLI, holds the area only for this test.
 */

const AREA = "settlements.employee_finance";
const PASSWORD = "root-password-123";

function createPlatformAdmin(email: string) {
  execFileSync(
    "uv",
    ["run", "--directory", "../backend", "python", "-m", "app.cli", "create-admin", "--email", email, "--name", "Root"],
    { input: `${PASSWORD}\n`, stdio: ["pipe", "ignore", "inherit"] },
  );
}

interface Account {
  id: number;
  code: string | null;
  balance: string;
}

async function mainCash(request: APIRequestContext) {
  const accounts = await api<Account[]>(request, "GET", "/settlements/accounts");
  return accounts.find((a) => a.code === "main_cash")!;
}

/** A settlement counted in full into the main cash box: $10.000 more there for the loans. */
async function fund(request: APIRequestContext, driverId: number) {
  const id = await openSettlement(request, driverId, "10000");
  const box = (await mainCash(request)).id;
  await api(request, "PUT", `/settlements/deliveries/${id}/count`, {
    lines: [{ denomination_code: "ARS_1000", quantity: 10, destination_account_id: box }],
  });
  const preview = await api<{ preview_token: string }>(request, "GET", `/settlements/deliveries/${id}/closing-preview`);
  await api(request, "POST", `/settlements/deliveries/${id}/close`, {
    version: 1,
    preview_token: preview.preview_token,
    allocations: [],
    confirm: true,
  });
}

async function noSideScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

const cents = (amount: string) => Math.round(Number(amount) * 100);

test("a loan with its schedule, deferrals in a chain, a reversal and privacy in Cajas", async ({ page }) => {
  const suffix = `${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const email = `e2e-r83-${suffix}@districom.local`;
  const name = `Empleado R8-3 ${suffix}`;
  createPlatformAdmin(email);
  await login(page, "/rendiciones", email, PASSWORD);
  await api(page.request, "PUT", "/auth/access-grants", { email, key: AREA, role: "admin" });
  const employeeId = await driver(page.request, name);
  await fund(page.request, employeeId);
  const before = await mainCash(page.request);

  // 1. Give a loan of $3.000 in 3 from the main cash box: the legs and the schedule before confirming.
  await page.goto(`/rendiciones/empleados/${employeeId}`);
  await page.getByRole("button", { name: "Dar préstamo" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("Importe del préstamo").fill("3.000");
  const from = dialog.getByLabel("Se entrega desde");
  await expect(from.locator("option", { hasText: "Caja principal" })).toHaveCount(1);
  await from.selectOption({ label: (await from.locator("option", { hasText: "Caja principal" }).textContent())! });
  await dialog.getByLabel("Cantidad de cuotas").fill("3");
  await dialog.getByRole("button", { name: "Revisar" }).click();
  const legs = dialog.getByTestId("loan-legs");
  await expect(legs).toContainText("Caja principal");
  await expect(legs).toContainText("-$ 3.000,00");
  await expect(legs).toContainText(`Cuenta interna · ${name}`);
  await expect(legs).toContainText("+$ 3.000,00");
  await expect(dialog.getByTestId("loan-totals")).toContainText("Nuevo cronograma$ 3.000,00");
  await expect(dialog.getByLabel("Importe de la cuota 3")).toHaveValue("1000,00");
  await noSideScroll(page);
  await dialog.getByRole("button", { name: "Confirmar y dar préstamo" }).click();
  await expect(page.getByRole("heading", { name: /^Préstamo n\.º \d+$/ })).toBeVisible();
  const loanId = Number(page.url().split("/").pop());
  await expect(page.getByTestId("loan-status")).toHaveText("Vigente");
  await expect(page.getByTestId("loan-facts")).toContainText("Saldo del préstamo$ 3.000,00");

  // 2. The cash box lost exactly the loan.
  const after = await mainCash(page.request);
  expect(cents(before.balance) - cents(after.balance)).toBe(300000);

  // 4. The schedule: three installments of $1.000, scheduled.
  for (const number of [1, 2, 3]) {
    await expect(page.getByTestId(`installment-${number}`)).toContainText("$ 1.000,00");
    await expect(page.getByTestId(`installment-${number}`)).toContainText("Pendiente de aplicar");
  }
  await noSideScroll(page);

  // 5. Defer installment 2 into two new ones ($400 + $600), with a reason.
  await page.getByTestId("installment-2").getByRole("button", { name: "Postergar" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Motivo").fill("Vacaciones");
  await dialog.getByRole("button", { name: "Agregar cuota" }).click();
  await dialog.getByLabel("Importe de la cuota 4").fill("400");
  await dialog.getByLabel("Importe de la cuota 5").fill("600");
  await expect(dialog.getByTestId("deferral-totals")).toContainText("Importe reemplazado$ 1.000,00");
  await expect(dialog.getByTestId("deferral-totals")).toContainText("Nuevo cronograma$ 1.000,00");
  await noSideScroll(page);
  await dialog.getByRole("button", { name: "Confirmar postergación" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("installment-2")).toContainText("Postergada");
  await expect(page.getByTestId("installment-4")).toContainText("reemplaza a la 2");
  await expect(page.getByTestId("installment-5")).toContainText("$ 600,00");

  // 6. Defer installment 4 again: a chain 2 → 4 → 6, every link kept in the history.
  await page.getByTestId("installment-4").getByRole("button", { name: "Postergar" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Motivo").fill("Pidió más plazo");
  await dialog.getByRole("button", { name: "Confirmar postergación" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("installment-6")).toContainText("reemplaza a la 4");
  await expect(page.getByTestId("deferral-2")).toContainText("Reemplazada por: Cuota 4 · $ 400,00");
  await expect(page.getByTestId("deferral-2")).toContainText("Motivo: Vacaciones");
  await expect(page.getByTestId("deferral-4")).toContainText("Reemplazada por: Cuota 6 · $ 400,00");
  await expect(page.getByTestId("loan-facts")).toContainText("Saldo del préstamo$ 3.000,00");
  await noSideScroll(page);

  // 3. The internal account: one movement of $3.000 for the loan, never one per installment, with its link.
  await page.getByRole("link", { name }).click();
  await expect(page.getByTestId("internal-balance")).toHaveText("$ 3.000,00");
  const rows = page.locator('[data-testid^="account-movement-"]');
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Préstamo");
  await expect(rows.first()).toContainText("+$ 3.000,00");
  await expect(rows.first()).toContainText("Le suma deuda");
  await expect(rows.first().getByRole("link", { name: `Ver préstamo n.º ${loanId}` })).toBeVisible();

  // 7. Another loan, reversed whole with a reason: the money returns, its installments are cancelled.
  await page.getByRole("button", { name: "Dar préstamo" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Importe del préstamo").fill("1.000");
  await dialog
    .getByLabel("Se entrega desde")
    .selectOption({ label: (await from.locator("option", { hasText: "Caja principal" }).textContent())! });
  await dialog.getByLabel("Cantidad de cuotas").fill("2");
  await dialog.getByLabel("Descripción (opcional)").fill("Cargado a otro empleado");
  await dialog.getByRole("button", { name: "Revisar" }).click();
  await dialog.getByRole("button", { name: "Confirmar y dar préstamo" }).click();
  await expect(page.getByRole("heading", { name: /^Préstamo n\.º \d+$/ })).toBeVisible();
  await page.getByRole("button", { name: "Revertir" }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Las cuotas programadas se cancelan");
  await dialog.getByLabel("Motivo del reverso").fill("Era para otro empleado");
  await dialog.getByRole("button", { name: "Confirmar reverso" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("loan-status")).toHaveText("Revertido");
  await expect(page.getByTestId("loan-facts")).toContainText("Saldo del préstamo$ 0,00");
  await expect(page.getByTestId("installment-1")).toContainText("Cancelada");
  await expect(page.getByTestId("installment-2")).toContainText("Cancelada");
  expect(cents((await mainCash(page.request)).balance)).toBe(cents(after.balance));
  await page.getByRole("link", { name }).click();
  await expect(page.getByTestId("internal-balance")).toHaveText("$ 3.000,00");
  await expect(rows).toHaveCount(3);
  await expect(page.getByText("Reverso de préstamo")).toBeVisible();
  await noSideScroll(page);

  // 8. Without the area the loan is only a reserved movement of the cash box: date, amount, box, direction.
  const loan = await api<{ operation_id: number }>(
    page.request,
    "GET",
    `/settlements/employee-finance/loans/${loanId}`,
  );
  await api(page.request, "POST", "/auth/access-grants/revoke", { email, key: AREA });
  await page.goto(`/rendiciones/cajas/operaciones/${loan.operation_id}`);
  await expect(page.getByText("Movimiento de empleado (reservado)").first()).toBeVisible();
  await expect(page.getByText("-$ 3.000,00").first()).toBeVisible();
  await expect(page.getByText(name)).toHaveCount(0);
  await expect(page.getByText(/Préstamo/)).toHaveCount(0);
  await expect(page.getByText(/[Cc]uota/)).toHaveCount(0);
  await noSideScroll(page);
});
