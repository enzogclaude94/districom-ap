import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

import { adminEmail, adminPassword, login } from "./helpers";

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";

const HEADERS = { "X-Requested-With": "e2e" };

async function api<T>(request: APIRequestContext, method: "GET" | "POST", path: string, data?: unknown) {
  const response = await request.fetch(`/api/v1${path}`, { method, headers: HEADERS, data });
  expect(response.ok(), `${method} ${path}: ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

function localDay(offsetDays: number) {
  const date = new Date(Date.now() + offsetDays * 24 * 60 * 60 * 1000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

async function switchUser(page: Page, email: string, password: string, next: string) {
  await page.context().clearCookies();
  await login(page, next, email, password);
}

/** The loads as the screen shows them: table rows on desktop, cards on mobile (only the visible ones). */
const loads = (scope: Page | Locator) =>
  scope.locator('[data-testid^="fuel-row-"]:visible, [data-testid^="fuel-card-"]:visible');

async function expectSummary(page: Page, liters: string, amount: string, price: string) {
  const summary = page.getByTestId("fuel-summary");
  await expect(summary).toContainText(`Litros${liters}`);
  await expect(summary).toContainText(`Gasto${amount}`);
  await expect(summary).toContainText(`Precio promedio${price}`);
}

async function closeSettlement(page: Page) {
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");
}

test("the fuel history follows a settlement's close, a direct expense, its reversal and a correction", async ({
  page,
}, testInfo) => {
  const mobile = testInfo.project.name === "mobile";
  const run = `${Date.now()}${mobile ? "M" : "D"}`;
  const plate = `HC${run.slice(-7)}`;
  const description = "Camión historial E2E";
  const driverName = `Repartidor historial ${run}`;

  // 1. Admin: the vehicle, a driver and money in the main box for the direct expense.
  await login(page, "/rendiciones/repartos", adminEmail, adminPassword);
  const truck = await api<{ id: number }>(page.request, "POST", "/vehicles", { plate, description });
  const driver = await api<{ id: number }>(page.request, "POST", "/employees", {
    full_name: driverName,
    hire_date: null,
    phone: null,
  });
  await api(page.request, "POST", `/employees/${driver.id}/functions`, {
    function_code: "settlements.delivery_driver",
    valid_from: "2026-01-01",
  });
  const accounts = await api<{ id: number; code: string | null }[]>(page.request, "GET", "/settlements/accounts");
  const mainCash = accounts.find((a) => a.code === "main_cash")!.id;
  await api(page.request, "POST", "/settlements/operations/opening-balance", {
    client_request_id: crypto.randomUUID(),
    business_date: localDay(0),
    account_id: mainCash,
    amount: "50000.00",
  });
  const history = `/rendiciones/combustible?vehiculo=${truck.id}`;

  // 2-3. Operator: a settlement of $20.000 whose only item is 10 l x $2.000 of fuel.
  await switchUser(page, operatorEmail, operatorPassword, "/rendiciones/repartos");
  await page.getByRole("button", { name: "Nueva rendición" }).click();
  const create = page.getByRole("dialog");
  await create.getByLabel("Fecha").fill(localDay(-1));
  await create.getByLabel("Repartidor").selectOption({ label: driverName });
  await create.getByLabel("Importe esperado").fill("20.000");
  await create.getByRole("button", { name: "Crear rendición" }).click();
  await expect(page).toHaveURL(/\/rendiciones\/repartos\/\d+$/);
  const settlement = new URL(page.url()).pathname;
  await page.getByRole("button", { name: "Registrar gasto" }).click();
  const expense = page.getByRole("dialog");
  await expense.getByLabel("Categoría").selectOption({ label: "Combustible" });
  await expense.getByLabel("Vehículo").selectOption({ label: `${plate} · ${description}` });
  await expense.getByLabel("Litros").fill("10");
  await expense.getByLabel("Precio por litro").fill("2.000");
  await expense.getByRole("button", { name: "Registrar gasto", exact: true }).click();
  await expect(expense).toBeHidden();

  // 4. Before the close: listed as in an open settlement, nothing in effect.
  await page.goto(history);
  await expect(loads(page)).toHaveCount(1);
  await expect(loads(page).first()).toContainText("En rendición abierta");
  await expectSummary(page, "0,000 l", "$ 0,00", "—");

  // 5-7. Close: published and summed.
  await page.goto(settlement);
  await closeSettlement(page);
  await page.goto(history);
  await expect(loads(page).first()).toContainText("Publicado");
  await expectSummary(page, "10,000 l", "$ 20.000,00", "$ 2.000,00/l");

  // 8-9. A direct fuel expense of 40 l x $1.250 from the main box: both add up.
  const direct = await api<{ id: number }>(page.request, "POST", "/settlements/expenses", {
    client_request_id: crypto.randomUUID(),
    business_date: localDay(0),
    category_id: (
      await api<{ id: number; code: string | null }[]>(page.request, "GET", "/settlements/expense-categories")
    ).find((c) => c.code === "FUEL")!.id,
    paid_from_account_id: mainCash,
    fuel: { vehicle_id: truck.id, employee_id: driver.id, liters: "40", price_per_liter: "1250" },
  });
  await page.reload();
  await expect(loads(page)).toHaveCount(2);
  await expectSummary(page, "50,000 l", "$ 70.000,00", "$ 1.400,00/l");

  // 10. Admin reverses it, from the link of its row.
  await switchUser(page, adminEmail, adminPassword, settlement);
  await page.goto(history);
  await page.getByRole("link", { name: `Gasto directo n.º ${direct.id} · Caja principal` }).click();
  await expect(page).toHaveURL(new RegExp(`/rendiciones/cajas/gastos/${direct.id}$`));
  await page.getByRole("button", { name: "Revertir gasto" }).click();
  const reverse = page.getByRole("dialog", { name: "Revertir gasto" });
  await reverse.getByLabel("Motivo").fill("Cargado por error (E2E)");
  await reverse.getByRole("button", { name: "Revertir gasto" }).click();
  await expect(reverse).toBeHidden();

  // 11-12. The row stays as reversed; only the settlement counts.
  await page.goto(history);
  await expect(loads(page)).toHaveCount(2);
  await expect(loads(page).filter({ hasText: "Revertido" })).toHaveCount(1);
  await expectSummary(page, "10,000 l", "$ 20.000,00", "$ 2.000,00/l");

  // 13-14. Reopen and correct the load to 8 l x $2.500 (same $20.000, still balanced).
  await page.goto(settlement);
  await page.getByRole("button", { name: "Reabrir para corregir" }).click();
  const reopen = page.getByRole("dialog");
  await reopen.getByLabel("Motivo").fill("Los litros estaban mal cargados");
  await reopen.getByRole("button", { name: "Reabrir", exact: true }).click();
  await expect(reopen).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("En corrección");
  await page
    .getByRole("row", { name: new RegExp(plate) })
    .getByRole("button", { name: "Corregir carga" })
    .click();
  const correct = page.getByRole("dialog");
  await correct.getByLabel("Litros").fill("8");
  await correct.getByLabel("Precio por litro").fill("2.500");
  await correct.getByRole("button", { name: "Guardar", exact: true }).click();
  await expect(correct).toBeHidden();

  // 15-16. The history shows the proposal; the summary keeps the close in effect.
  await page.goto(history);
  const correcting = loads(page).filter({ hasText: "En corrección" });
  await expect(correcting).toContainText("8,000 l");
  await expect(correcting).toContainText("Cierre vigente: 10,000 l · $ 2.000,00/l");
  await expectSummary(page, "10,000 l", "$ 20.000,00", "$ 2.000,00/l");

  // 17-18. The new close moves the summary.
  await page.goto(settlement);
  await closeSettlement(page);
  await page.goto(history);
  await expect(loads(page).filter({ hasText: "Publicado" })).toContainText("8,000 l");
  await expectSummary(page, "8,000 l", "$ 20.000,00", "$ 2.500,00/l");

  // 19. From the whole history, filtering by the vehicle with the filter itself.
  await page.goto("/rendiciones/combustible");
  await page.getByLabel("Vehículo").selectOption({ label: `${plate} · ${description}` });
  await expect(loads(page)).toHaveCount(2);
  await expectSummary(page, "8,000 l", "$ 20.000,00", "$ 2.500,00/l");
  await page.getByLabel("Estado").selectOption({ label: "Anulado" });
  await expect(page.getByText("No existen cargas con estos filtros")).toBeVisible();

  // 20. The vehicle's page: its loads apart from the master's changes, and the way back to the history.
  await page.goto(`/vehiculos/${truck.id}`);
  await expect(page.getByText("Cambios del maestro")).toBeVisible();
  const vehicleLoads = page.getByTestId("vehicle-fuel-loads");
  await expect(vehicleLoads.getByText("Cargas de combustible")).toBeVisible();
  await expect(loads(vehicleLoads)).toHaveCount(2);
  await page.getByRole("link", { name: "Ver historial completo de combustible" }).click();
  await expect(page).toHaveURL(new RegExp(`/rendiciones/combustible\\?vehiculo=${truck.id}$`));
  await expect(loads(page)).toHaveCount(2);

  // Mobile: cards, filters and the page fit the screen, nothing scrolls sideways.
  if (mobile) {
    await expect(page.getByTestId("fuel-table")).toBeHidden();
    await expect(page.getByTestId("fuel-cards")).toBeVisible();
    await expect(page.getByLabel("Vehículo")).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  }
});
