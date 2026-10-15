import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

import { adminEmail, adminPassword, businessDay, login } from "./helpers";

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";

const HEADERS = { "X-Requested-With": "e2e" };
const DRIVER_FUNCTION = "settlements.delivery_driver";

interface Account {
  id: number;
  code: string | null;
  name: string;
  kind: string;
  balance: string;
}

/** Yesterday in the business calendar, resolved when used: never a future date for the backend. */
const yesterday = () => businessDay(-1);

const cents = (amount: string) => Math.round(Number(amount) * 100);

async function api<T>(
  request: APIRequestContext,
  method: "GET" | "POST" | "PUT" | "PATCH",
  path: string,
  data?: unknown,
) {
  const response = await request.fetch(`/api/v1${path}`, { method, headers: HEADERS, data });
  expect(response.ok(), `${method} ${path}: ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

async function employee(request: APIRequestContext, name: string, driver: boolean) {
  const created = await api<{ id: number }>(request, "POST", "/employees", {
    full_name: name,
    hire_date: null,
    phone: null,
  });
  if (driver) {
    await api(request, "POST", `/employees/${created.id}/functions`, {
      function_code: DRIVER_FUNCTION,
      valid_from: "2026-01-01",
    });
  }
  return created.id;
}

async function balances(request: APIRequestContext) {
  const accounts = await api<Account[]>(request, "GET", "/settlements/accounts?include_inactive=true");
  return accounts;
}

async function switchUser(page: Page, email: string, password: string, next: string) {
  await page.context().clearCookies();
  await login(page, next, email, password);
}

/** Opens a dialog with `button`, fills it and submits it; waits until it closes. */
async function dialogAction(page: Page, button: Locator, fill: (dialog: Locator) => Promise<void>, submit: string) {
  await button.click();
  const dialog = page.getByRole("dialog");
  await fill(dialog);
  await dialog.getByRole("button", { name: submit, exact: true }).click();
  await expect(dialog).toBeHidden();
}

async function newSettlement(page: Page, day: string, driverName: string, expected: string) {
  await page.goto("/rendiciones/repartos");
  await page.getByRole("button", { name: "Nueva rendición" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Fecha").fill(day);
  await dialog.getByLabel("Repartidor").selectOption({ label: driverName });
  await dialog.getByLabel("Importe esperado").fill(expected);
  return dialog;
}

test("delivery settlement end to end: load, shortage between two employees, close, reopen, corrected close", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "full flow runs on desktop");
  const run = Date.now();
  const day = yesterday();
  const driverName = `Repartidor E2E ${run}`;
  const helperName = `Ayudante E2E ${run}`;

  // 1. Admin: the driver, a helper and an expense category; then the operator creates the settlement.
  await login(page, "/rendiciones/repartos", adminEmail, adminPassword);
  const driverId = await employee(page.request, driverName, true);
  const helperId = await employee(page.request, helperName, false);
  const category = `Peajes E2E ${run}`;
  await api(page.request, "POST", "/settlements/expense-categories", { name: category });
  const before = Object.fromEntries((await balances(page.request)).filter((a) => a.code).map((a) => [a.code, a]));

  await switchUser(page, operatorEmail, operatorPassword, "/rendiciones/repartos");
  await expect(page.getByRole("heading", { name: "Rendiciones de reparto" })).toBeVisible();
  const create = await newSettlement(page, day, driverName, "100.000");
  await create.getByLabel("Referencia (opcional)").fill(`HR-${run}`);
  await create.getByRole("button", { name: "Crear rendición" }).click();
  await expect(page).toHaveURL(/\/rendiciones\/repartos\/\d+$/);
  const settlementId = Number(page.url().split("/").pop());
  await expect(page.getByRole("heading", { name: `Rendición n.º 1 · ${driverName}` })).toBeVisible();
  await expect(page.getByTestId("financial-state")).toHaveText("Sin cerrar");

  // 2. Count: 3 × $20.000 to Caja principal (suggested) and 20 × $500 to Caja de cambio (suggested).
  await dialogAction(
    page,
    page.getByRole("button", { name: "Cargar conteo" }),
    async (dialog) => {
      await dialog.getByLabel("Cantidad de billetes de $ 20.000,00").fill("3");
      await dialog.getByLabel("Cantidad de billetes de $ 500,00").fill("20");
      await expect(dialog.getByTestId("count-total")).toHaveText("$ 70.000,00");
    },
    "Guardar conteo",
  );

  // 3. Addition of $500 to Caja principal.
  await dialogAction(
    page,
    page.getByRole("button", { name: "Agregar efectivo" }),
    async (dialog) => {
      await dialog.getByLabel("Importe").fill("500");
      await dialog.getByLabel("Caja de destino").selectOption({ label: "Caja principal" });
      await dialog.getByLabel("Descripción (opcional)").fill("Monedas juntadas");
    },
    "Agregar",
  );
  await expect(page.getByRole("row", { name: /Monedas juntadas/ })).toContainText("Vigente");

  // 4. Declared transfer of $20.000: it blocks the close.
  await dialogAction(
    page,
    page.getByRole("button", { name: "Declarar transferencia" }),
    async (dialog) => {
      await dialog.getByLabel("Importe").fill("20.000");
      await dialog.getByLabel("Quién pagó (opcional)").fill("Almacén E2E");
      await dialog.getByLabel("Referencia (opcional)").fill(`OP-${run}`);
    },
    "Declarar",
  );
  const transferRow = page.getByRole("row", { name: /Almacén E2E/ });
  await expect(transferRow).toContainText("Declarada");
  await expect(transferRow).toContainText("Bloquea el cierre");

  // 5. Verify it.
  await dialogAction(page, transferRow.getByRole("button", { name: "Verificar" }), async () => {}, "Verificar");
  await expect(transferRow).toContainText("Verificada");

  // 6. Expense of $5.000.
  await dialogAction(
    page,
    page.getByRole("button", { name: "Registrar gasto" }),
    async (dialog) => {
      await dialog.getByLabel("Categoría").selectOption({ label: category });
      await dialog.getByLabel("Importe").fill("5.000");
    },
    "Registrar gasto",
  );

  // 7. Shortage: 70.000 + 500 + 20.000 + 5.000 = 95.500 against 100.000.
  await expect(page.getByTestId("board-difference")).toHaveText("Faltante $ 4.500,00");

  // 8-9. Preview, and the shortage split between the driver and the helper.
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const close = page.getByRole("dialog");
  await expect(close.getByLabel("Importes del cierre")).toContainText("$ 95.500,00");
  const confirm = close.getByRole("button", { name: "Confirmar cierre" });
  await expect(confirm).toBeDisabled();
  await close.getByLabel("Importe de la parte 1").fill("3.000");
  await expect(close.getByTestId("remaining")).toHaveText("$ 1.500,00");
  await close.getByRole("button", { name: "Agregar otra parte" }).click();
  await close.getByLabel("Parte 2: quién").selectOption({ label: helperName });
  await close.getByLabel("Importe de la parte 2").fill("1.500");
  await expect(close.getByTestId("remaining")).toHaveText("$ 0,00");

  // 10. Close.
  await confirm.click();
  await expect(close).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");
  await expect(page.getByRole("status")).toContainText("Rendición cerrada");

  // 11. Cajas: the close is there, as a settlement close that cannot be reversed from Cajas.
  await page.getByRole("link", { name: "Ver el cierre en Cajas" }).first().click();
  await expect(page).toHaveURL(/\/rendiciones\/cajas\/operaciones\/\d+$/);
  await expect(page.getByText("Cierre de rendición").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Revertir" })).toHaveCount(0);
  const afterFirst = Object.fromEntries((await balances(page.request)).map((a) => [a.code ?? a.name, a]));
  expect(cents(afterFirst.main_cash.balance) - cents(before.main_cash.balance)).toBe(6_050_000);
  expect(cents(afterFirst.change_cash.balance) - cents(before.change_cash.balance)).toBe(1_000_000);
  expect(cents(afterFirst.bank.balance) - cents(before.bank.balance)).toBe(2_000_000);
  // The operator has no Empleados area (ADR 010): Cajas does not list internal accounts to them.
  expect(Object.keys(afterFirst).some((name) => name.startsWith("Cuenta interna"))).toBe(false);

  // 12. Admin reopens with a reason: Cajas keep the close until the new one.
  await switchUser(page, adminEmail, adminPassword, `/rendiciones/repartos/${settlementId}`);
  const withArea = Object.fromEntries((await balances(page.request)).map((a) => [a.code ?? a.name, a]));
  const internal = (id: number, name: string) => withArea[`Cuenta interna de ${name} (#${id})`];
  expect(internal(driverId, driverName).balance).toBe("3000.00");
  expect(internal(helperId, helperName).balance).toBe("1500.00");
  await dialogAction(
    page,
    page.getByRole("button", { name: "Reabrir para corregir" }),
    (dialog) => dialog.getByLabel("Motivo").fill("Apareció el efectivo que faltaba"),
    "Reabrir",
  );
  await expect(page.getByTestId("financial-state")).toHaveText("En corrección");
  await expect(
    page.getByText(
      "Esta rendición está siendo corregida. El cierre anterior continúa vigente en Cajas hasta que confirmes el nuevo cierre.",
    ),
  ).toBeVisible();
  const whileCorrecting = Object.fromEntries((await balances(page.request)).map((a) => [a.code ?? a.name, a]));
  expect(whileCorrecting.main_cash.balance).toBe(afterFirst.main_cash.balance);

  // 13. The missing $4.500 turns up.
  await dialogAction(
    page,
    page.getByRole("button", { name: "Agregar efectivo" }),
    async (dialog) => {
      await dialog.getByLabel("Importe").fill("4.500");
      await dialog.getByLabel("Caja de destino").selectOption({ label: "Caja principal" });
    },
    "Agregar",
  );
  await expect(page.getByTestId("board-difference")).toHaveText("Balanceada");

  // 14-15. New preview, balanced, confirmed explicitly.
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const second = page.getByRole("dialog");
  await expect(second.getByTestId("balanced")).toHaveText("Rendición balanceada — Diferencia $0");
  await expect(second).toContainText("el cierre anterior se revierte");
  await second.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(second).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");

  // 16. Net effect: the correction only added the $4.500; the internal accounts are back to zero.
  const afterSecond = Object.fromEntries((await balances(page.request)).map((a) => [a.code ?? a.name, a]));
  expect(cents(afterSecond.main_cash.balance) - cents(before.main_cash.balance)).toBe(6_500_000);
  expect(cents(afterSecond.change_cash.balance) - cents(before.change_cash.balance)).toBe(1_000_000);
  expect(cents(afterSecond.bank.balance) - cents(before.bank.balance)).toBe(2_000_000);
  expect(afterSecond[`Cuenta interna de ${driverName} (#${driverId})`].balance).toBe("0.00");
  expect(afterSecond[`Cuenta interna de ${helperName} (#${helperId})`].balance).toBe("0.00");

  // 17. History: original close, reopening, reversal and new close, in order.
  await page.getByRole("tab", { name: "Historial" }).click();
  const history = page.getByRole("list", { name: "Historial de la rendición" });
  await expect(history.locator(":scope > li")).toHaveText([
    /Rendición creada/,
    /^Cierre/,
    /Faltante de \$ 4\.500,00 \(versión 1\)/,
    /Reapertura \(versión 1 → 2\).*Apareció el efectivo que faltaba/,
    /Reverso del cierre de la versión 1/,
    /Nuevo cierre \(versión 2\)/,
  ]);

  // 18. A second settlement for the same driver and day: warned, confirmed, numbered 2.
  const again = await newSettlement(page, day, driverName, "50.000");
  await expect(again.getByTestId("same-day-warning")).toContainText(`${driverName} ya tiene una rendición`);
  await again.getByRole("button", { name: "Crear rendición" }).click();
  await expect(again.getByText("Confirmá que es otra rendición del mismo día.")).toBeVisible();
  await again.getByLabel("Sí, es otra rendición del mismo día").check();
  await again.getByRole("button", { name: "Crear rendición" }).click();
  await expect(page.getByRole("heading", { name: `Rendición n.º 2 · ${driverName}` })).toBeVisible();
});

test("a close that confirms an outdated preview is refused and confirmed again on the new one", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "full flow runs on desktop");
  const run = Date.now();
  const day = yesterday();
  const driverName = `Repartidor vista previa ${run}`;

  await login(page, "/rendiciones/repartos", adminEmail, adminPassword);
  const driverId = await employee(page.request, driverName, true);
  const accounts = await balances(page.request);
  const main = accounts.find((a) => a.code === "main_cash")!;
  const change = accounts.find((a) => a.code === "change_cash")!;
  const created = await api<{ id: number }>(page.request, "POST", "/settlements/deliveries", {
    client_request_id: crypto.randomUUID(),
    business_date: day,
    employee_id: driverId,
    expected_amount: "1000.00",
  });
  const count = (box: number) =>
    api(page.request, "PUT", `/settlements/deliveries/${created.id}/count`, {
      lines: [{ denomination_code: "ARS_1000", quantity: 1, destination_account_id: box }],
    });
  await count(main.id);

  // Preview on screen…
  await page.goto(`/rendiciones/repartos/${created.id}`);
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByTestId("balanced")).toBeVisible();
  await expect(dialog.getByLabel("Importes del cierre")).toContainText("Efectivo en Caja principal");

  // …then someone moves the banknote to the other box: same totals, different close.
  await count(change.id);
  await dialog.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(
    dialog.getByText(
      "La rendición cambió desde que la revisaste. Actualizamos los datos para que vuelvas a confirmarla.",
    ),
  ).toBeVisible();
  await expect(dialog.getByLabel("Importes del cierre")).toContainText("Efectivo en Caja de cambio");
  await expect(page.getByTestId("financial-state")).toHaveText("Sin cerrar");

  // The new preview is confirmed again, explicitly.
  await dialog.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");
});

test("the settlements list is usable on a phone", async ({ page }) => {
  await login(page, "/rendiciones/repartos", operatorEmail, operatorPassword);
  await expect(page.getByRole("heading", { name: "Rendiciones de reparto" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Nueva rendición" })).toBeVisible();
  await expect(page.getByLabel("Estado")).toBeVisible();
});

test("an admin manages expense categories without deleting them or changing earlier expenses", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "full flow runs on desktop");
  const run = Date.now();
  const name = `Lavado E2E ${run}`;

  await login(page, "/rendiciones/configuracion", adminEmail, adminPassword);
  await expect(page.getByRole("heading", { name: "Configuración de Rendiciones" })).toBeVisible();
  const input = page.getByLabel("Nueva categoría");
  await input.fill(`  ${name.replace(" ", "   ")} `);
  await page.getByRole("button", { name: "Crear categoría" }).click();
  await expect(page.getByRole("status")).toHaveText(`Categoría «${name}» creada.`);
  await input.fill(name.toUpperCase());
  await page.getByRole("button", { name: "Crear categoría" }).click();
  await expect(page.getByText(`Ya existe la categoría «${name.toUpperCase()}».`)).toBeVisible();

  // An expense with it in an open settlement.
  const driverId = await employee(page.request, `Repartidor categorías ${run}`, true);
  const categories = await api<{ id: number; name: string }[]>(page.request, "GET", "/settlements/expense-categories");
  const categoryId = categories.find((c) => c.name === name)!.id;
  const settlement = await api<{ id: number }>(page.request, "POST", "/settlements/deliveries", {
    client_request_id: crypto.randomUUID(),
    business_date: yesterday(),
    employee_id: driverId,
    expected_amount: "1000.00",
  });
  await api(page.request, "POST", `/settlements/deliveries/${settlement.id}/expenses`, {
    client_request_id: crypto.randomUUID(),
    category_id: categoryId,
    amount: "300.00",
  });

  // Deactivate and rename it: the expense keeps its category and amount; new expenses cannot use it.
  await page.goto("/rendiciones/configuracion");
  await page.getByRole("button", { name: `Desactivar ${name}` }).click();
  await expect(page.getByRole("status")).toHaveText(`«${name}» quedó desactivada.`);
  const renamed = `${name} camiones`;
  await page
    .getByRole("row", { name: new RegExp(name) })
    .getByRole("button", { name: "Renombrar" })
    .click();
  await page.getByRole("dialog").getByLabel("Nombre").fill(renamed);
  await page.getByRole("dialog").getByRole("button", { name: "Guardar" }).click();
  await expect(page.getByRole("row", { name: new RegExp(renamed) })).toContainText("Desactivada");

  await page.goto(`/rendiciones/repartos/${settlement.id}`);
  const expenseRow = page.getByRole("row", { name: new RegExp(renamed) });
  await expect(expenseRow).toContainText("$ 300,00");
  await expect(expenseRow).toContainText("Vigente");
  await page.getByRole("button", { name: "Registrar gasto" }).click();
  await expect(page.getByRole("dialog").getByLabel("Categoría").locator("option", { hasText: renamed })).toHaveCount(0);
  await page.keyboard.press("Escape");

  // Active again: offered for new expenses.
  await page.goto("/rendiciones/configuracion");
  await page.getByRole("button", { name: `Activar ${renamed}` }).click();
  await expect(page.getByRole("row", { name: new RegExp(renamed) })).toContainText("Activa");
  await page.goto(`/rendiciones/repartos/${settlement.id}`);
  await page.getByRole("button", { name: "Registrar gasto" }).click();
  await expect(page.getByRole("dialog").getByLabel("Categoría").locator("option", { hasText: renamed })).toHaveCount(1);
});
