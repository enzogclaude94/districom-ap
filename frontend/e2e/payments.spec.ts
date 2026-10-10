import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { adminEmail, adminPassword, businessDay, login } from "./helpers";
import { chequeForPending, closedWithPending, driver } from "./settlement-setup";

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";

const HEADERS = { "X-Requested-With": "e2e" };

interface Account {
  id: number;
  code: string | null;
  balance: string;
}

async function api<T>(request: APIRequestContext, method: "GET" | "POST", path: string, data?: unknown) {
  const response = await request.fetch(`/api/v1${path}`, { method, headers: HEADERS, data });
  expect(response.ok(), `${method} ${path}: ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

const cents = (amount: string) => Math.round(Number(amount) * 100);

async function account(request: APIRequestContext, code: string) {
  const all = await api<Account[]>(request, "GET", "/settlements/accounts?include_inactive=true");
  return all.find((a) => a.code === code)!;
}

const today = () => businessDay();

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test("an operator pays a new third party with cash and a transfer after a confirmation; an admin reverses it", async ({
  page,
}, testInfo) => {
  const mobile = testInfo.project.name === "mobile";
  const run = `${Date.now()}${mobile ? "M" : "D"}`;
  const supplier = `Proveedor E2E ${run}`;

  // Admin: money in the main cash box and in the bank.
  await login(page, "/rendiciones/pagos", adminEmail, adminPassword);
  const main = await account(page.request, "main_cash");
  const bank = await account(page.request, "bank");
  for (const target of [main, bank]) {
    await api(page.request, "POST", "/settlements/operations/opening-balance", {
      client_request_id: crypto.randomUUID(),
      business_date: today(),
      account_id: target.id,
      amount: "300000.00",
    });
  }
  const cashBefore = cents((await account(page.request, "main_cash")).balance);
  const bankBefore = cents((await account(page.request, "bank")).balance);

  // Operator: Registrar pago, registering the supplier on the spot.
  await page.context().clearCookies();
  await login(page, "/rendiciones/pagos", operatorEmail, operatorPassword);
  await expect(page.getByRole("heading", { name: "Pagos" })).toBeVisible();
  await page.getByRole("button", { name: "Registrar pago" }).click();
  const form = page.getByRole("dialog", { name: "Registrar pago" });
  await form.getByRole("button", { name: "Nuevo tercero" }).click();
  const create = page.getByRole("dialog", { name: "Nuevo tercero" });
  await create.getByLabel("Nombre o razón social").fill(supplier);
  await create.getByRole("button", { name: "Registrar y usar" }).click();

  await expect(form.getByLabel("Tercero")).toHaveValue(/\d+/);
  await expect(form.getByLabel("Tercero").locator("option:checked")).toHaveText(supplier);
  await form.getByLabel("Concepto").selectOption("MERCHANDISE");
  await form.getByLabel("Total del pago").fill("300.000");
  await form.getByLabel("Caja").selectOption(String(main.id));
  await form.getByLabel("Importe del medio 1").fill("100.000");
  await expect(form.getByTestId("payment-balance")).toContainText("Falta$ 200.000,00");
  await form.getByRole("button", { name: "Agregar medio de pago" }).click();
  await form.getByLabel("Banco").selectOption(String(bank.id));
  await form.getByLabel("Importe del medio 2").fill("200.000");
  await form.getByLabel("Referencia del medio 2 (opcional)").fill("TRF E2E");
  await form.getByLabel("Referencia (opcional)").fill("FC A 0001-E2E");
  if (mobile) await noHorizontalScroll(page);
  await form.getByRole("button", { name: "Revisar pago" }).click();

  const confirm = page.getByRole("dialog", { name: "Confirmá el pago" });
  const summary = confirm.getByTestId("payment-confirmation");
  await expect(summary).toContainText(`Pago por mercadería a ${supplier}`);
  await expect(summary).toContainText("Caja principal-$ 100.000,00");
  await expect(summary).toContainText("Banco-$ 200.000,00");
  await expect(summary).toContainText("Pagos por mercadería+$ 300.000,00");
  expect(cents((await account(page.request, "main_cash")).balance)).toBe(cashBefore); // nothing paid yet
  await confirm.getByRole("button", { name: "Confirmar pago" }).click();
  await expect(confirm).toBeHidden();
  const notice = page.getByRole("status").filter({ hasText: "registrado por $ 300.000,00" });
  await expect(notice).toBeVisible();
  expect(cents((await account(page.request, "main_cash")).balance)).toBe(cashBefore - 10_000_000);
  expect(cents((await account(page.request, "bank")).balance)).toBe(bankBefore - 20_000_000);
  if (mobile) await noHorizontalScroll(page);

  await notice.getByRole("link", { name: "Ver el pago" }).click();
  await expect(page).toHaveURL(/\/rendiciones\/pagos\/\d+$/);
  await expect(page.getByTestId("payment-items")).toContainText("Transferencia · Banco");
  await expect(page.getByRole("button", { name: "Revertir pago" })).toHaveCount(0); // operator
  const detail = new URL(page.url()).pathname;

  // Admin: reverse the whole payment with a reason; the money is back.
  await page.context().clearCookies();
  await login(page, detail, adminEmail, adminPassword);
  await page.getByRole("button", { name: "Revertir pago" }).click();
  const reverse = page.getByRole("dialog", { name: "Revertir pago" });
  await reverse.getByLabel("Motivo").fill("Pago cargado por error (E2E)");
  await reverse.getByRole("button", { name: "Revertir pago" }).click();
  await expect(reverse).toBeHidden();
  await expect(page.getByTestId("payment-state")).toHaveText("Revertido");
  expect(cents((await account(page.request, "main_cash")).balance)).toBe(cashBefore);
  expect(cents((await account(page.request, "bank")).balance)).toBe(bankBefore);
  if (mobile) await noHorizontalScroll(page);

  // The supplier's ficha (platform) shows the quick create and, for Rendiciones users, the payment.
  await page.getByRole("link", { name: supplier }).click();
  await expect(page.getByRole("heading", { name: supplier })).toBeVisible();
  await expect(page.getByText("Registrado desde un pago de Rendiciones")).toBeVisible();
  await expect(page.getByTestId("third-party-payments")).toContainText("Revertido");
});

test("a cheque of the portfolio is delivered whole in a mixed payment; the reversal needs it recovered", async ({
  page,
}, testInfo) => {
  const mobile = testInfo.project.name === "mobile";
  const run = `${Date.now()}${mobile ? "M" : "D"}`;
  const number = `7${String(Date.now()).slice(-6)}${mobile ? 1 : 2}`;
  const supplier = `Proveedor cheques E2E ${run}`;

  // Admin (setup by API): a cheque of $40.000 in the portfolio, cash in the main box and the supplier.
  await login(page, "/rendiciones/pagos", adminEmail, adminPassword);
  const driverId = await driver(page.request, `Repartidor pagos ${run}`);
  const customer = `Almacén pagos ${run}`;
  const pendingId = await closedWithPending(page.request, driverId, customer, "40000.00");
  const chequeId = await chequeForPending(page.request, pendingId, "40000.00", number, customer);
  const main = await account(page.request, "main_cash");
  await api(page.request, "POST", "/settlements/operations/opening-balance", {
    client_request_id: crypto.randomUUID(),
    business_date: today(),
    account_id: main.id,
    amount: "50000.00",
  });
  await api(page.request, "POST", "/third-parties", { name: supplier, tax_id: null, notes: null });
  const cashBefore = cents((await account(page.request, "main_cash")).balance);
  const portfolioBefore = cents((await account(page.request, "cheque_portfolio")).balance);

  // Operator: «Entregar» on the cheque opens «Registrar pago» with it preloaded.
  await page.context().clearCookies();
  await login(page, `/rendiciones/cheques/${chequeId}`, operatorEmail, operatorPassword);
  await page.getByRole("button", { name: "Entregar" }).click();
  await expect(page).toHaveURL(new RegExp(`/rendiciones/pagos\\?cheque=${chequeId}$`));
  const form = page.getByRole("dialog", { name: "Registrar pago" });
  await expect(form.getByLabel("Cheque de la cartera")).toHaveValue(String(chequeId));
  await expect(form.getByLabel("Importe del medio 1")).toHaveValue("$ 40.000,00");
  await expect(form.getByLabel("Importe del medio 1")).toHaveAttribute("readonly", "");
  await form.getByLabel("Tercero").selectOption({ label: supplier });
  await form.getByLabel("Concepto").selectOption("MERCHANDISE");
  await form.getByLabel("Total del pago").fill("50.000");
  await expect(form.getByTestId("payment-balance")).toContainText("Falta$ 10.000,00");
  await form.getByRole("button", { name: "Agregar medio de pago" }).click();
  await form.getByLabel("Medio de pago").nth(1).selectOption("CASH");
  await form.getByLabel("Caja").selectOption(String(main.id));
  await form.getByLabel("Importe del medio 2").fill("10.000");
  if (mobile) await noHorizontalScroll(page);
  await form.getByRole("button", { name: "Revisar pago" }).click();

  const confirm = page.getByRole("dialog", { name: "Confirmá el pago" });
  const summary = confirm.getByTestId("payment-confirmation");
  await expect(summary).toContainText(`Cheque n.º ${number} desde Cartera de cheques-$ 40.000,00`);
  await expect(summary).toContainText("Caja principal-$ 10.000,00");
  await expect(summary).toContainText("Pagos por mercadería+$ 50.000,00");
  await confirm.getByRole("button", { name: "Confirmar pago" }).click();
  await expect(confirm).toBeHidden();
  const notice = page.getByRole("status").filter({ hasText: "registrado por $ 50.000,00" });
  await expect(notice).toBeVisible();
  expect(cents((await account(page.request, "main_cash")).balance)).toBe(cashBefore - 1_000_000);
  expect(cents((await account(page.request, "cheque_portfolio")).balance)).toBe(portfolioBefore - 4_000_000);

  await notice.getByRole("link", { name: "Ver el pago" }).click();
  await expect(page.getByTestId(`payment-cheque-state-${chequeId}`)).toHaveText("Entregado");
  const detail = new URL(page.url()).pathname;
  if (mobile) await noHorizontalScroll(page);

  // The cheque: «Entregado» to the supplier, with nothing left to deposit or deliver.
  await page.getByRole("link", { name: `n.º ${number}` }).click();
  await expect(page.getByTestId("cheque-state")).toHaveText("Entregado");
  await expect(page.getByTestId("cheque-delivery")).toContainText(`Entregado a ${supplier}`);
  await expect(page.getByRole("button", { name: "Depositar" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Entregar" })).toHaveCount(0);
  await expect(page.getByTestId(/^document-PAYMENT-/)).toContainText("Entregado a tercero");

  // Admin: no reversal until the cheque is confirmed as recovered; then it is back in the portfolio.
  await page.context().clearCookies();
  await login(page, detail, adminEmail, adminPassword);
  await page.getByRole("button", { name: "Revertir pago" }).click();
  const reverse = page.getByRole("dialog", { name: "Revertir pago" });
  await reverse.getByLabel("Motivo").fill("El proveedor devolvió el cheque (E2E)");
  await reverse.getByRole("button", { name: "Revertir pago" }).click();
  await expect(reverse.getByRole("alert")).toContainText("Confirmá que recuperaste cada cheque");
  await reverse.getByRole("checkbox", { name: new RegExp(`Recuperé el cheque n.º ${number}`) }).check();
  await reverse.getByRole("button", { name: "Revertir pago" }).click();
  await expect(reverse).toBeHidden();
  await expect(page.getByTestId("payment-state")).toHaveText("Revertido");
  await expect(page.getByTestId(`payment-cheque-recovered-${chequeId}`)).toContainText(
    "Cheque recuperado y devuelto a Cartera",
  );
  expect(cents((await account(page.request, "main_cash")).balance)).toBe(cashBefore);
  expect(cents((await account(page.request, "cheque_portfolio")).balance)).toBe(portfolioBefore);
  await page.getByRole("link", { name: "Ver cheque" }).click();
  await expect(page.getByTestId("cheque-state")).toHaveText("En cartera");
  if (mobile) await noHorizontalScroll(page);
});
