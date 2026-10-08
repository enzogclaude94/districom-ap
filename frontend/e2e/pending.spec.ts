import { execFileSync } from "node:child_process";

import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

import { formatMoney } from "../src/lib/format";
import { adminEmail, adminPassword, login } from "./helpers";

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

interface PendingRef {
  id: number;
  customer_name: string;
}

/** Yesterday in the business calendar (the browser runs in the business time zone, see playwright.config). */
function yesterday() {
  const now = new Date(Date.now() - 24 * 60 * 60 * 1000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Argentina/Buenos_Aires" }).format(now);
}

const cents = (amount: string) => Math.round(Number(amount) * 100);
const money = (value: number) => formatMoney((value / 100).toFixed(2));

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

async function accounts(request: APIRequestContext) {
  return api<Account[]>(request, "GET", "/settlements/accounts?include_inactive=true");
}

/** Balance in cents of the account with this code, or of an employee's internal account by name. */
async function balance(request: APIRequestContext, which: { code: string } | { employee: string }) {
  const all = await accounts(request);
  const account =
    "code" in which
      ? all.find((a) => a.code === which.code)
      : all.find((a) => a.kind === "EMPLOYEE" && a.name.startsWith(`Cuenta interna de ${which.employee} (`));
  return account ? cents(account.balance) : 0;
}

async function switchUser(page: Page, email: string, password: string, next: string) {
  await page.context().clearCookies();
  await login(page, next, email, password);
}

async function dialogAction(page: Page, button: Locator, fill: (dialog: Locator) => Promise<void>, submit: string) {
  await button.click();
  const dialog = page.getByRole("dialog");
  await fill(dialog);
  await dialog.getByRole("button", { name: submit, exact: true }).click();
  await expect(dialog).toBeHidden();
}

/** A closed settlement whose only justification is one pending of `amount` (API: the setup is not under test). */
async function closedWithPending(request: APIRequestContext, driverId: number, customer: string, amount: string) {
  const created = await api<{ id: number }>(request, "POST", "/settlements/deliveries", {
    client_request_id: crypto.randomUUID(),
    business_date: yesterday(),
    employee_id: driverId,
    expected_amount: amount,
  });
  const loaded = await api<{ pending_collections: PendingRef[] }>(
    request,
    "POST",
    `/settlements/deliveries/${created.id}/pending-collections`,
    { client_request_id: crypto.randomUUID(), customer_name: customer, amount, reason: "Paga la semana próxima" },
  );
  const preview = await api<{ preview_token: string }>(
    request,
    "GET",
    `/settlements/deliveries/${created.id}/closing-preview`,
  );
  await api(request, "POST", `/settlements/deliveries/${created.id}/close`, {
    version: 1,
    preview_token: preview.preview_token,
    allocations: [],
    confirm: true,
  });
  return { settlementId: created.id, pendingId: loaded.pending_collections[0].id };
}

async function receiptFromList(
  page: Page,
  customer: string,
  amount: string,
  method: "Efectivo" | "Transferencia",
  to: string,
) {
  await page.getByRole("button", { name: `Registrar cobro de ${customer}` }).click();
  const dialog = page.getByRole("dialog", { name: "Registrar cobro" });
  await expect(dialog.getByText(customer)).toBeVisible();
  await dialog.getByLabel("Importe").fill(amount);
  await dialog.getByRole("radio", { name: method }).check();
  await dialog.getByLabel(method === "Efectivo" ? "Caja de destino" : "Banco de destino").selectOption({ label: to });
  await dialog.getByRole("button", { name: "Revisar cobro" }).click();
  // Nothing is credited before this explicit confirmation.
  await expect(dialog.getByRole("group", { name: "Revisá el cobro" })).toContainText(customer);
  await dialog.getByRole("button", { name: "Confirmar cobro" }).click();
  await expect(dialog).toBeHidden();
}

async function resolve(page: Page, amount: string, part: string, reason: string) {
  await page.getByRole("button", { name: "Resolver saldo" }).click();
  const dialog = page.getByRole("dialog", { name: "Resolver saldo" });
  await dialog.getByLabel("Importe a resolver").fill(amount);
  await dialog.getByLabel("A cargo de, parte 1").selectOption({ label: part });
  const confirm = dialog.getByRole("button", { name: "Confirmar resolución" });
  await dialog.getByLabel("Motivo").fill(reason);
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel("Importe, parte 1").fill(amount);
  await expect(dialog.getByTestId("resolution-missing")).toHaveText("$ 0,00");
  await confirm.click();
  await expect(dialog).toBeHidden();
}

test("pending collection end to end: settlement, close, partial receipt, resolutions, reversal and history", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "full flow runs on desktop");
  const run = Date.now();
  const driverName = `Repartidor pendientes ${run}`;
  const helperName = `Ayudante pendientes ${run}`;
  const customer = `Almacén E2E ${run}`;

  await login(page, "/rendiciones", adminEmail, adminPassword);
  const driverId = await employee(page.request, driverName, true);
  await employee(page.request, helperName, false);
  const before = {
    main: await balance(page.request, { code: "main_cash" }),
    pending: await balance(page.request, { code: "pending_collections" }),
    uncollectible: await balance(page.request, { code: "uncollectible" }),
  };

  // 1–3. Operator: settlement with a $100.000 pending, closed from the screen.
  await switchUser(page, operatorEmail, operatorPassword, "/rendiciones/repartos");
  await page.getByRole("button", { name: "Nueva rendición" }).click();
  const create = page.getByRole("dialog");
  await create.getByLabel("Fecha").fill(yesterday());
  await create.getByLabel("Repartidor").selectOption({ label: driverName });
  await create.getByLabel("Importe esperado").fill("100.000");
  await create.getByRole("button", { name: "Crear rendición" }).click();
  await expect(page).toHaveURL(/\/rendiciones\/repartos\/\d+$/);
  const settlementId = Number(page.url().split("/").pop());

  await dialogAction(
    page,
    page.getByRole("button", { name: "Agregar pendiente" }),
    async (dialog) => {
      await dialog.getByLabel("Cliente").fill(customer);
      await dialog.getByLabel("Importe").fill("100.000");
      await dialog.getByLabel("Motivo").fill("No tenía cambio");
      // The driver answers for it unless someone else is chosen.
      await expect(dialog.getByLabel("Responsable de cobrarlo")).toHaveValue(String(driverId));
    },
    "Agregar pendiente",
  );
  const row = page.locator('[data-testid^="pending-row-"]').filter({ hasText: customer });
  await expect(row).toContainText("En rendición abierta");
  await expect(page.getByTestId("board-difference")).toHaveText("Balanceada");

  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const close = page.getByRole("dialog");
  await close.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(close).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");
  await expect(row).toContainText("Pendiente");

  // 4. Cajas: Pendientes de cobro +$100.000.
  expect(await balance(page.request, { code: "pending_collections" })).toBe(before.pending + 10_000_000);

  // 5–7. Global list shows it OPEN; the operator collects $60.000 in cash with the quick action.
  await page.goto("/rendiciones/pendientes");
  // Other specs leave pendings too: search the customer so the row is on the first page.
  await page.getByLabel("Cliente", { exact: true }).fill(customer);
  const listed = page.locator('[data-testid^="pending-item-"]').filter({ hasText: customer });
  await expect(listed).toContainText("Pendiente");
  await expect(listed).toContainText("$ 100.000,00");
  await receiptFromList(page, customer, "60.000", "Efectivo", "Caja principal");
  await expect(listed).toContainText("Parcial");
  await expect(listed).toContainText("Cobrado: $ 60.000,00 · Resuelto: $ 0,00 · Saldo: $ 40.000,00");
  // The operator has no administrative actions on the pending.
  await listed.getByRole("link", { name: customer }).click();
  await expect(page.getByTestId("pending-balance")).toHaveText("$ 40.000,00");
  await expect(page.getByTestId("pending-state")).toHaveText("Parcial");
  await expect(page.getByRole("button", { name: "Resolver saldo" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Revertir" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Cambiar responsable" })).toHaveCount(0);
  const pendingUrl = new URL(page.url()).pathname;

  // 8–10. Admin resolves $20.000 to the company and $20.000 to the helper.
  await switchUser(page, adminEmail, adminPassword, pendingUrl);
  await resolve(page, "20.000", "Empresa (Incobrables)", "Cliente cerró el local");
  await expect(page.getByTestId("pending-balance")).toHaveText("$ 20.000,00");
  await resolve(page, "20.000", helperName, "Lo entregó sin autorización");
  await expect(page.getByTestId("pending-balance")).toHaveText("$ 0,00");
  await expect(page.getByTestId("pending-state")).toHaveText("Resuelto");

  // 11–13. Caja principal, Incobrables, the helper's account and Pendientes de cobro, from the API.
  expect(await balance(page.request, { code: "main_cash" })).toBe(before.main + 6_000_000);
  expect(await balance(page.request, { code: "uncollectible" })).toBe(before.uncollectible + 2_000_000);
  expect(await balance(page.request, { employee: helperName })).toBe(2_000_000);
  expect(await balance(page.request, { code: "pending_collections" })).toBe(before.pending);

  // 14–15. Reverse the helper's resolution: the balance reopens and the account goes back.
  const helperResolution = page.locator('[data-testid^="resolution-"]').filter({ hasText: helperName });
  await dialogAction(
    page,
    helperResolution.getByRole("button", { name: "Revertir" }),
    async (dialog) => {
      await expect(dialog).toContainText("El movimiento original no se borra");
      await dialog.getByLabel("Motivo").fill("Se cargó al empleado equivocado");
    },
    "Revertir",
  );
  await expect(helperResolution).toContainText("Revertido");
  await expect(page.getByTestId("pending-balance")).toHaveText("$ 20.000,00");
  await expect(page.getByTestId("pending-state")).toHaveText("Parcial");
  expect(await balance(page.request, { employee: helperName })).toBe(0);
  expect(await balance(page.request, { code: "pending_collections" })).toBe(before.pending + 2_000_000);

  // 16. Full history, from the pending's own events.
  const history = page.getByRole("list", { name: "Historia del pendiente" });
  for (const event of [
    "Pendiente registrado",
    "Publicado en Cajas por el cierre",
    "Cobro registrado",
    "Saldo resuelto",
    "Resolución revertida",
  ]) {
    await expect(history.getByText(event, { exact: true }).first()).toBeVisible();
  }

  // Cajas ↔ pending: the receipt's operation links back to the pending and offers no reversal there.
  await page
    .getByTestId(/^receipt-/)
    .getByRole("link", { name: "Ver en Cajas" })
    .click();
  await expect(page.getByRole("heading", { name: /Cobro de pendiente/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Revertir operación" })).toHaveCount(0);
  await page
    .getByRole("link", { name: /^Pendiente de cobro #\d+$/ })
    .first()
    .click();
  await expect(page).toHaveURL(new RegExp(`${pendingUrl}$`));
  await expect(page.getByRole("link", { name: /Rendición n.º 1/ })).toHaveAttribute(
    "href",
    `/rendiciones/repartos/${settlementId}`,
  );
});

test("a pending collected in full by transfer goes to the bank and is Cobrado", async ({ page, isMobile }) => {
  test.skip(isMobile, "full flow runs on desktop");
  const run = Date.now();
  const customer = `Kiosco transferencia ${run}`;
  await login(page, "/rendiciones/pendientes", adminEmail, adminPassword);
  const driverId = await employee(page.request, `Repartidor transferencia ${run}`, true);
  await closedWithPending(page.request, driverId, customer, "50000.00");
  const bank = await balance(page.request, { code: "bank" });

  await switchUser(page, operatorEmail, operatorPassword, "/rendiciones/pendientes");
  await page.getByLabel("Cliente", { exact: true }).fill(customer);
  const listed = page.locator('[data-testid^="pending-item-"]').filter({ hasText: customer });
  await page.getByRole("button", { name: `Registrar cobro de ${customer}` }).click();
  const dialog = page.getByRole("dialog", { name: "Registrar cobro" });
  await expect(dialog.getByTestId("receipt-balance")).toHaveText("$ 50.000,00");
  await dialog.getByRole("button", { name: "Cobrar saldo completo" }).click();
  await dialog.getByRole("radio", { name: "Transferencia" }).check();
  // Cash boxes are not offered for a transfer.
  await expect(dialog.getByLabel("Banco de destino").locator("option", { hasText: "Caja principal" })).toHaveCount(0);
  await dialog.getByLabel("Banco de destino").selectOption({ label: "Banco" });
  await dialog.getByRole("button", { name: "Revisar cobro" }).click();
  await expect(dialog.getByTestId("receipt-amount")).toHaveText("$ 50.000,00");
  await dialog.getByRole("button", { name: "Confirmar cobro" }).click();
  await expect(dialog).toBeHidden();

  // Collected pendings leave the default list (OPEN and PARTIAL) and show with their state filter.
  await expect(listed).toHaveCount(0);
  await page.getByLabel("Estado").selectOption({ label: "Cobrado" });
  await expect(listed).toContainText("Cobrado");
  expect(await balance(page.request, { code: "bank" })).toBe(bank + 5_000_000);
});

test("a correction cannot leave a pending below what was collected, and closes with a valid amount", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "full flow runs on desktop");
  const run = Date.now();
  const customer = `Despensa corrección ${run}`;
  await login(page, "/rendiciones", adminEmail, adminPassword);
  const driverId = await employee(page.request, `Repartidor corrección ${run}`, true);
  const { settlementId, pendingId } = await closedWithPending(page.request, driverId, customer, "100000.00");
  const main = (await accounts(page.request)).find((a) => a.code === "main_cash")!;
  await api(page.request, "POST", `/settlements/pending-collections/${pendingId}/receipts`, {
    client_request_id: crypto.randomUUID(),
    amount: "60000.00",
    method: "CASH",
    destination_account_id: main.id,
  });

  await page.goto(`/rendiciones/repartos/${settlementId}`);
  await dialogAction(
    page,
    page.getByRole("button", { name: "Reabrir para corregir" }),
    async (dialog) => dialog.getByLabel("Motivo").fill("El cliente devolvió mercadería"),
    "Reabrir",
  );
  await expect(page.getByTestId("financial-state")).toHaveText("En corrección");

  const row = page.getByTestId(`pending-row-${pendingId}`);
  await row.getByRole("button", { name: "Corregir" }).click();
  const edit = page.getByRole("dialog");
  await edit.getByLabel("Importe").fill("50.000");
  await edit.getByRole("button", { name: "Guardar" }).click();
  await expect(edit.getByRole("alert")).toContainText(
    "El pendiente ya tiene $ 60.000,00 cobrados o resueltos: su importe no puede quedar por debajo.",
  );
  await edit.getByLabel("Importe").fill("80.000");
  await edit.getByRole("button", { name: "Guardar" }).click();
  await expect(edit).toBeHidden();

  // The correction is a proposal: in effect stays $100.000 until the new close.
  await expect(row.getByTestId("load-change")).toContainText("Vigente: $ 100.000,00");
  await expect(row.getByTestId("load-change")).toContainText("Nuevo importe propuesto: $ 80.000,00");
  const pending = await api<{ amount_in_effect: string; state: string }>(
    page.request,
    "GET",
    `/settlements/pending-collections/${pendingId}`,
  );
  expect([pending.amount_in_effect, pending.state]).toEqual(["100000.00", "PARTIAL"]);

  // The returned goods' $20.000 came back as cash: the settlement balances and closes again.
  await dialogAction(
    page,
    page.getByRole("button", { name: "Agregar efectivo" }),
    async (dialog) => dialog.getByLabel("Importe").fill("20.000"),
    "Agregar",
  );
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const close = page.getByRole("dialog");
  await close.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(close).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");
  await expect(row).toContainText("$ 80.000,00");

  await page.goto(`/rendiciones/pendientes/${pendingId}`);
  await expect(page.getByTestId("amount-in-effect")).toHaveText("$ 80.000,00");
  await expect(page.getByTestId("pending-balance")).toHaveText("$ 20.000,00");
});

test("an old pending without collection alerts on the home page and in the menu", async ({ page, isMobile }) => {
  test.skip(isMobile, "full flow runs on desktop");
  const run = Date.now();
  const customer = `Bar alerta ${run}`;
  await login(page, "/rendiciones", adminEmail, adminPassword);
  const driverId = await employee(page.request, `Repartidor alerta ${run}`, true);
  const { pendingId } = await closedWithPending(page.request, driverId, customer, "30000.00");
  const settings = await api<{ alert_hours: number }>(page.request, "GET", "/settlements/pending-alert-settings");
  expect(settings.alert_hours).toBe(24);
  const before = await api<{ count: number; balance: string }>(
    page.request,
    "GET",
    "/settlements/pending-collections/summary",
  );

  // Registered three days ago (only the age changes; see age_pending.py).
  execFileSync("uv", [
    "run",
    "--directory",
    "../backend",
    "python",
    "../frontend/e2e/age_pending.py",
    String(pendingId),
    "72",
  ]);

  await page.goto("/rendiciones");
  const card = page.getByTestId("pending-alert-card");
  const count = before.count + 1;
  await expect(card.getByTestId("alert-count")).toHaveText(
    count === 1 ? "1 pendiente en alerta" : `${count} pendientes en alerta`,
  );
  await expect(card.getByTestId("alert-balance")).toHaveText(`${money(cents(before.balance) + 3_000_000)} pendientes`);
  await expect(page.getByTestId("pending-alert-badge")).toHaveText(String(count));

  await card.getByRole("link", { name: "Ver pendientes" }).click();
  await expect(page).toHaveURL(/\/rendiciones\/pendientes\?alerta=1$/);
  await expect(page.getByLabel("Solo en alerta")).toBeChecked();
  const listed = page.getByTestId(`pending-item-${pendingId}`);
  await expect(listed.getByTestId("alert-mark")).toContainText("En alerta · 3 días");
  await expect(listed.getByTestId("alert-mark")).toContainText("Saldo pendiente: $ 30.000,00");
});

test("an admin changes the alert hours from the module configuration", async ({ page, isMobile }) => {
  test.skip(isMobile, "runs on desktop");
  await login(page, "/rendiciones/configuracion", adminEmail, adminPassword);
  const card = page.getByTestId("pending-alert-settings");
  const field = card.getByLabel("Alertar pendientes después de");
  await expect(field).toHaveValue("24");
  await expect(card).toContainText(
    "Un pendiente con saldo aparecerá en alerta cuando supere este tiempo desde su creación.",
  );
  await field.fill("0");
  await card.getByRole("button", { name: "Guardar" }).click();
  await expect(card.getByRole("alert")).toContainText("de 1 a 720 horas");
  await field.fill("48");
  await card.getByRole("button", { name: "Guardar" }).click();
  await expect(card.getByRole("status")).toContainText("Guardado");
  expect(
    (await api<{ alert_hours: number }>(page.request, "GET", "/settlements/pending-alert-settings")).alert_hours,
  ).toBe(48);
  // Back to the default for the other specs.
  await field.fill("24");
  await card.getByRole("button", { name: "Guardar" }).click();
  await expect(card.getByRole("status")).toContainText("Guardado");
});

test("pendings are usable on a phone", async ({ page }) => {
  await login(page, "/rendiciones/pendientes", operatorEmail, operatorPassword);
  await expect(page.getByRole("heading", { name: "Pendientes de cobro" })).toBeVisible();
  await expect(page.getByLabel("Solo en alerta")).toBeVisible();
  const width = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(width).toBeLessThanOrEqual(0);
  const first = page.locator('[data-testid^="pending-item-"] a').first();
  if (await first.count()) {
    await first.click();
    await expect(page.getByTestId("pending-balance")).toBeVisible();
    await expect(page.getByRole("button", { name: "Registrar cobro" })).toBeVisible();
    await page.getByRole("button", { name: "Registrar cobro" }).click();
    const dialog = page.getByRole("dialog", { name: "Registrar cobro" });
    await expect(dialog.getByRole("button", { name: "Revisar cobro" })).toBeVisible();
    const overflow = await dialog.evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  }
});
