import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

import { adminEmail, adminPassword, login } from "./helpers";
import { api, chequeForPending, closedWithPending, day, driver, openSettlement } from "./settlement-setup";

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";

interface Account {
  id: number;
  code: string | null;
  name: string;
  kind: string;
  balance: string;
}

const cents = (amount: string) => Math.round(Number(amount) * 100);

async function account(request: APIRequestContext, code: string) {
  const all = await api<Account[]>(request, "GET", "/settlements/accounts?include_inactive=true");
  return all.find((a) => a.code === code)!;
}

const balance = async (request: APIRequestContext, code: string) => cents((await account(request, code)).balance);

async function switchUser(page: Page, email: string, password: string, next: string) {
  await page.context().clearCookies();
  await login(page, next, email, password);
}

/** Fill the instrument form (settlement or pending receipt). */
async function fillCheque(
  dialog: Locator,
  values: {
    kind?: "Físico" | "ECHEQ";
    amount: string;
    number: string;
    deliveredBy?: string;
    taxId?: string;
    account?: string;
  },
) {
  if (values.kind) await dialog.getByRole("radio", { name: values.kind, exact: true }).check();
  await dialog.getByLabel("Importe del cheque").fill(values.amount);
  await dialog.getByLabel("Banco emisor").selectOption({ label: "BANCO DE GALICIA Y BUENOS AIRES S.A. (7)" });
  await dialog.getByLabel("Número", { exact: true }).fill(values.number);
  await dialog.getByLabel("Fecha de emisión").fill(day(-1));
  await dialog.getByLabel("Titular / librador").fill("Librador E2E SRL");
  if (values.taxId) await dialog.getByLabel("CUIT del librador").fill(values.taxId);
  if (values.account) await dialog.getByLabel("CBU", { exact: true }).fill(values.account);
  if (values.deliveredBy) await dialog.getByLabel("Quién lo entregó").fill(values.deliveredBy);
}

async function closeFromScreen(page: Page) {
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const close = page.getByRole("dialog");
  await close.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(close).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");
}

async function depositFromDetail(page: Page) {
  await page.getByRole("button", { name: "Depositar" }).click();
  const dialog = page.getByRole("dialog", { name: "Crear boleta de depósito" });
  await dialog.getByLabel("Banco destino").selectOption({ label: "Banco" });
  await dialog.getByRole("button", { name: "Revisar", exact: true }).click();
  await dialog.getByRole("button", { name: "Confirmar depósito" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("cheque-state")).toHaveText("Depositado");
}

test("A. physical cheque in a settlement: close, deposit and credit from the portfolio", async ({ page, isMobile }) => {
  test.skip(isMobile, "full flow runs on desktop");
  const run = Date.now();
  const number = String(run).slice(-8);
  await login(page, "/rendiciones", adminEmail, adminPassword);
  const driverId = await driver(page.request, `Repartidor cheques A ${run}`);
  const settlementId = await openSettlement(page.request, driverId, "100000.00");
  const before = {
    portfolio: await balance(page.request, "cheque_portfolio"),
    transit: await balance(page.request, "cheques_in_transit"),
    bank: await balance(page.request, "bank"),
  };

  await switchUser(page, operatorEmail, operatorPassword, `/rendiciones/repartos/${settlementId}`);
  await page.getByRole("button", { name: "Cargar cheque" }).click();
  const load = page.getByRole("dialog", { name: "Cargar cheque" });
  await fillCheque(load, { amount: "100.000", number, deliveredBy: "Almacén cheque A" });
  await load.getByRole("button", { name: "Cargar cheque" }).click();
  await expect(load.getByRole("alert")).toContainText("Recibí el cheque físicamente");
  await load.getByRole("checkbox", { name: /Recibí el cheque físicamente/ }).check();
  await load.getByRole("button", { name: "Cargar cheque" }).click();
  await expect(load).toBeHidden();
  const row = page.locator('[data-testid^="cheque-row-"]').filter({ hasText: number });
  await expect(row).toContainText("Justifica la rendición");
  await expect(page.getByTestId("board-difference")).toHaveText("Balanceada");
  await closeFromScreen(page);
  expect(await balance(page.request, "cheque_portfolio")).toBe(before.portfolio + 10_000_000);

  // Cajas: «Valores — no disponibles para gastar».
  await page.goto("/rendiciones/cajas");
  await expect(page.getByRole("heading", { name: "Valores — no disponibles para gastar" })).toBeVisible();
  await page.getByTestId("values-portfolio").click();
  await expect(page).toHaveURL(/\/rendiciones\/cheques\?state=IN_PORTFOLIO$/);

  const listed = page.locator('[data-testid^="cheque-"]').filter({ hasText: number });
  await expect(listed).toContainText("En cartera");
  await listed.getByRole("checkbox", { name: `Seleccionar el cheque n.º ${number}` }).check();
  await page.getByRole("button", { name: "Crear boleta de depósito" }).click();
  const deposit = page.getByRole("dialog", { name: "Crear boleta de depósito" });
  await expect(deposit.getByTestId("cycle-total")).toHaveText("$ 100.000,00");
  await deposit.getByLabel("Banco destino").selectOption({ label: "Banco" });
  await deposit.getByLabel("Número de boleta (opcional)").fill(`B-${number}`);
  await deposit.getByRole("button", { name: "Revisar", exact: true }).click();
  // Nothing is deposited before the summary is confirmed.
  expect(await balance(page.request, "cheques_in_transit")).toBe(before.transit);
  await expect(deposit.getByRole("group", { name: "Revisá antes de confirmar" })).toContainText(
    "Sin cambio hasta la acreditación",
  );
  await deposit.getByRole("button", { name: "Confirmar depósito" }).click();
  await expect(deposit).toBeHidden();
  await expect(page.getByRole("status")).toContainText("Boleta de depósito registrada");
  expect(await balance(page.request, "cheque_portfolio")).toBe(before.portfolio);
  expect(await balance(page.request, "cheques_in_transit")).toBe(before.transit + 10_000_000);

  await page.getByLabel("Estado").selectOption({ label: "Depositado" });
  await listed.getByRole("checkbox", { name: `Seleccionar el cheque n.º ${number}` }).check();
  await page.getByRole("button", { name: "Registrar acreditación" }).click();
  const credit = page.getByRole("dialog", { name: "Registrar acreditación" });
  await credit.getByRole("button", { name: "Revisar", exact: true }).click();
  await credit.getByRole("button", { name: "Confirmar acreditación" }).click();
  await expect(credit).toBeHidden();
  expect(await balance(page.request, "cheques_in_transit")).toBe(before.transit);
  expect(await balance(page.request, "bank")).toBe(before.bank + 10_000_000);

  // The document shows the slip and links to the cheque; the detail shows the history.
  await page.getByRole("status").getByRole("link", { name: "Ver documento" }).click();
  await expect(page.getByRole("heading", { name: /Acreditación n.º \d+/ })).toBeVisible();
  await page.getByRole("link", { name: `N.º ${number}`, exact: true }).click();
  await expect(page.getByTestId("cheque-state")).toHaveText("Acreditado");
  for (const event of ["Recibido", "Entró a cartera", "Depositado", "Acreditado"]) {
    await expect(page.getByText(event, { exact: true }).first()).toBeVisible();
  }
});

test("B. a declared ECHEQ blocks the close until it is verified", async ({ page, isMobile }) => {
  test.skip(isMobile, "full flow runs on desktop");
  const run = Date.now();
  const number = `2${String(run).slice(-7)}`;
  await login(page, "/rendiciones", adminEmail, adminPassword);
  const driverId = await driver(page.request, `Repartidor cheques B ${run}`);
  const settlementId = await openSettlement(page.request, driverId, "40000.00");

  await switchUser(page, operatorEmail, operatorPassword, `/rendiciones/repartos/${settlementId}`);
  await page.getByRole("button", { name: "Cargar cheque" }).click();
  const load = page.getByRole("dialog", { name: "Cargar cheque" });
  await fillCheque(load, {
    kind: "ECHEQ",
    amount: "40.000",
    number,
    deliveredBy: "Almacén ECHEQ B",
    taxId: "30711222333",
    account: "0070999030004123456789",
  });
  await load.getByRole("button", { name: "Cargar cheque" }).click();
  await expect(load).toBeHidden();
  const row = page.locator('[data-testid^="cheque-row-"]').filter({ hasText: number });
  await expect(row).toContainText("Pendiente de verificación: bloquea el cierre");

  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const close = page.getByRole("dialog");
  // A declared ECHEQ justifies nothing yet (the difference shows a shortage) and blocks the close.
  await expect(close.getByRole("alert").filter({ hasText: "No se puede cerrar todavía" })).toContainText(
    "ECHEQ declarados sin verificar",
  );
  await expect(close.getByRole("button", { name: "Confirmar cierre" })).toBeDisabled();
  await close.getByRole("button", { name: "Cancelar" }).click();

  await row.getByRole("button", { name: "Verificar" }).click();
  const verify = page.getByRole("dialog", { name: "Verificar ECHEQ" });
  await verify.getByRole("button", { name: "Verificar" }).click();
  await expect(verify).toBeHidden();
  await expect(row).toContainText("Justifica la rendición");
  await closeFromScreen(page);
});

test("C. pending collected with a cheque, rejected in transit, new pending collected in cash", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "full flow runs on desktop");
  const run = Date.now();
  const number = `3${String(run).slice(-7)}`;
  const customer = `Kiosco cheque C ${run}`;
  await login(page, "/rendiciones", adminEmail, adminPassword);
  const driverId = await driver(page.request, `Repartidor cheques C ${run}`);
  const pendingId = await closedWithPending(page.request, driverId, customer, "60000.00");
  const pending = await balance(page.request, "pending_collections");

  await switchUser(page, operatorEmail, operatorPassword, `/rendiciones/pendientes/${pendingId}`);
  await page.getByRole("button", { name: "Registrar cobro" }).click();
  const receipt = page.getByRole("dialog", { name: "Registrar cobro" });
  await receipt.getByRole("radio", { name: "Cheque" }).check();
  // Larger than the balance: blocked, there are no credit balances.
  await fillCheque(receipt, { amount: "70.000", number });
  await expect(receipt.getByText(/El importe del cheque supera el saldo pendiente/).first()).toBeVisible();
  await receipt.getByLabel("Importe del cheque").fill("60.000");
  await expect(receipt.getByLabel("Quién lo entregó")).toHaveValue(customer);
  await receipt.getByRole("checkbox", { name: /Recibí el cheque físicamente/ }).check();
  await receipt.getByRole("button", { name: "Revisar cobro" }).click();
  await expect(receipt.getByRole("group", { name: "Revisá el cobro" })).toContainText("Cartera de cheques+$ 60.000,00");
  await receipt.getByRole("button", { name: "Confirmar cobro" }).click();
  await receipt.getByRole("link", { name: "Ver cheque" }).click();
  await expect(page.getByTestId("cheque-state")).toHaveText("En cartera");
  expect(await balance(page.request, "pending_collections")).toBe(pending - 6_000_000);

  await depositFromDetail(page);
  await page.getByRole("button", { name: "Registrar rechazo" }).click();
  const reject = page.getByRole("dialog", { name: "Registrar rechazo" });
  await expect(reject).toContainText("En tránsito");
  await expect(reject).toContainText(
    `El rechazo volverá a reconocer una deuda por $ 60.000,00 a nombre de ${customer}.`,
  );
  await expect(reject.getByTestId("rejection-overdraft-warning")).toHaveCount(0);
  await reject.getByLabel("Motivo bancario").fill("Sin fondos suficientes");
  await reject.getByRole("button", { name: "Revisar", exact: true }).click();
  await reject.getByRole("button", { name: "Confirmar rechazo" }).click();
  await expect(reject).toBeHidden();
  await expect(page.getByTestId("cheque-state")).toHaveText("Rechazado");
  expect(await balance(page.request, "pending_collections")).toBe(pending);

  await page.getByTestId("new-pending-link").click();
  await expect(page.getByTestId("pending-balance")).toHaveText("$ 60.000,00");
  await page.getByRole("button", { name: "Registrar cobro" }).click();
  const cash = page.getByRole("dialog", { name: "Registrar cobro" });
  await cash.getByRole("button", { name: "Cobrar saldo completo" }).click();
  await cash.getByLabel("Caja de destino").selectOption({ label: "Caja principal" });
  await cash.getByRole("button", { name: "Revisar cobro" }).click();
  await cash.getByRole("button", { name: "Confirmar cobro" }).click();
  await expect(cash).toBeHidden();
  await expect(page.getByTestId("pending-state")).toHaveText("Cobrado");
  expect(await balance(page.request, "pending_collections")).toBe(pending - 6_000_000);
});

test("D. a credited cheque rejected overdraws the bank; an income brings it back", async ({ page, isMobile }) => {
  test.skip(isMobile, "full flow runs on desktop");
  const run = Date.now();
  const customer = `Distribuidora cheque D ${run}`;
  await login(page, "/rendiciones", adminEmail, adminPassword);
  const driverId = await driver(page.request, `Repartidor cheques D ${run}`);
  const pendingId = await closedWithPending(page.request, driverId, customer, "80000.00");
  const chequeId = await chequeForPending(page.request, pendingId, "80000.00", `4${String(run).slice(-7)}`, customer);
  const bank = await account(page.request, "bank");
  const main = await account(page.request, "main_cash");
  await api(page.request, "POST", "/settlements/cheque-deposits", {
    client_request_id: crypto.randomUUID(),
    bank_account_id: bank.id,
    cheque_ids: [chequeId],
    business_date: day(-1),
  });

  await switchUser(page, operatorEmail, operatorPassword, `/rendiciones/cheques/${chequeId}`);
  await page.getByRole("button", { name: "Registrar acreditación" }).click();
  const credit = page.getByRole("dialog", { name: "Registrar acreditación" });
  await credit.getByRole("button", { name: "Revisar", exact: true }).click();
  await credit.getByRole("button", { name: "Confirmar acreditación" }).click();
  await expect(credit).toBeHidden();
  await expect(page.getByTestId("cheque-state")).toHaveText("Acreditado");

  // The money was spent: the whole bank goes to the main cash box (setup).
  const spent = (await account(page.request, "bank")).balance;
  await api(page.request, "POST", "/settlements/operations/bank-withdrawal", {
    client_request_id: crypto.randomUUID(),
    business_date: day(0),
    bank_account_id: bank.id,
    cash_account_id: main.id,
    amount: spent,
  });

  await page.reload();
  await page.getByRole("button", { name: "Registrar rechazo" }).click();
  const reject = page.getByRole("dialog", { name: "Registrar rechazo" });
  await expect(reject).toContainText("Después de acreditado");
  await expect(reject.getByTestId("rejection-overdraft-warning")).toHaveText(
    "El banco será debitado por $ 80.000,00. Esto puede dejar la cuenta en descubierto.",
  );
  await reject.getByLabel("Motivo bancario").fill("Firma no coincide");
  await reject.getByRole("button", { name: "Revisar", exact: true }).click();
  await reject.getByRole("button", { name: "Confirmar rechazo" }).click();
  await expect(reject).toBeHidden();
  await expect(page.getByTestId("new-pending-link")).toBeVisible();
  expect(await balance(page.request, "bank")).toBe(-8_000_000);

  await page.goto("/rendiciones/cajas");
  const overdraft = page.getByTestId(`overdraft-${bank.id}`);
  await expect(overdraft).toContainText("Banco en descubierto");
  await expect(overdraft).toContainText("-$ 80.000,00");
  await expect(overdraft.getByRole("link", { name: "Ver cheque" }).first()).toHaveAttribute(
    "href",
    `/rendiciones/cheques/${chequeId}`,
  );
  await expect(overdraft.getByRole("button")).toHaveCount(0);

  // An income reduces it: the customer pays the new pending by transfer.
  await page.goto(`/rendiciones/cheques/${chequeId}`);
  await page.getByRole("link", { name: "Ver pendiente creado" }).click();
  await page.getByRole("button", { name: "Registrar cobro" }).click();
  const transfer = page.getByRole("dialog", { name: "Registrar cobro" });
  await transfer.getByRole("button", { name: "Cobrar saldo completo" }).click();
  await transfer.getByRole("radio", { name: "Transferencia" }).check();
  await transfer.getByLabel("Banco de destino").selectOption({ label: "Banco" });
  await transfer.getByRole("button", { name: "Revisar cobro" }).click();
  await transfer.getByRole("button", { name: "Confirmar cobro" }).click();
  await expect(transfer).toBeHidden();
  expect(await balance(page.request, "bank")).toBe(0);
  await page.goto("/rendiciones/cajas");
  await expect(page.getByRole("button", { name: "Ver movimientos de Banco" })).toBeVisible();
  await expect(page.getByTestId(`overdraft-${bank.id}`)).toHaveCount(0);

  // Leave the bank as it was for the other specs.
  await switchUser(page, adminEmail, adminPassword, "/rendiciones");
  await api(page.request, "POST", "/settlements/operations/bank-deposit", {
    client_request_id: crypto.randomUUID(),
    business_date: day(0),
    cash_account_id: main.id,
    bank_account_id: bank.id,
    amount: spent,
  });
});

test("E. an admin returns a cheque of the portfolio and the debt is recognized again", async ({ page, isMobile }) => {
  test.skip(isMobile, "full flow runs on desktop");
  const run = Date.now();
  const customer = `Almacén devolución E ${run}`;
  await login(page, "/rendiciones", adminEmail, adminPassword);
  const driverId = await driver(page.request, `Repartidor cheques E ${run}`);
  const pendingId = await closedWithPending(page.request, driverId, customer, "30000.00");
  const chequeId = await chequeForPending(page.request, pendingId, "30000.00", `5${String(run).slice(-7)}`, customer);
  const portfolio = await balance(page.request, "cheque_portfolio");

  // An operator cannot return.
  await switchUser(page, operatorEmail, operatorPassword, `/rendiciones/cheques/${chequeId}`);
  await expect(page.getByRole("button", { name: "Depositar" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Devolver cheque" })).toHaveCount(0);
  await expect(page.getByText("Entregar cheque")).toHaveCount(0);

  await switchUser(page, adminEmail, adminPassword, `/rendiciones/cheques/${chequeId}`);
  await page.getByRole("button", { name: "Devolver cheque" }).click();
  const dialog = page.getByRole("dialog", { name: "Devolver cheque" });
  await expect(dialog).toContainText("El cheque saldrá de Cartera y volverá a reconocerse una deuda por $ 30.000,00");
  await dialog.getByLabel("Motivo").fill("El cliente lo reemplaza por efectivo");
  await dialog.getByRole("button", { name: "Revisar", exact: true }).click();
  await dialog.getByRole("button", { name: "Confirmar devolución" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("cheque-state")).toHaveText("Devuelto");
  expect(await balance(page.request, "cheque_portfolio")).toBe(portfolio - 3_000_000);
  await page.getByTestId("new-pending-link").click();
  await expect(page.getByTestId("pending-balance")).toHaveText("$ 30.000,00");
});

test("F. the cheque list and detail work on a phone", async ({ page, isMobile }) => {
  test.skip(!isMobile, "mobile layout");
  const run = Date.now();
  const number = `6${String(run).slice(-7)}`;
  await login(page, "/rendiciones", adminEmail, adminPassword);
  const driverId = await driver(page.request, `Repartidor cheques F ${run}`);
  const customer = `Kiosco móvil ${run}`;
  const pendingId = await closedWithPending(page.request, driverId, customer, "25000.00");
  await chequeForPending(page.request, pendingId, "25000.00", number, customer);

  await page.goto("/rendiciones/cheques");
  const cards = page.getByRole("list", { name: "Cheques" });
  await expect(cards).toBeVisible();
  const card = cards.getByRole("listitem").filter({ hasText: number });
  await expect(card).toContainText("$ 25.000,00");
  await expect(card).toContainText("En cartera");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await card.getByRole("link", { name: `N.º ${number}`, exact: true }).click();
  await expect(page.getByTestId("cheque-state")).toHaveText("En cartera");
  await expect(page.getByText("Presentar hasta").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Depositar" })).toBeVisible();
});
