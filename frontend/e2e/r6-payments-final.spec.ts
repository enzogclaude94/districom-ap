import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { adminEmail, adminPassword, businessDay, login } from "./helpers";
import { api, galicia } from "./settlement-setup";

/**
 * Final R6 flow (R6-3): a payment with cash, a transfer and two cheques of the portfolio to a third party, its
 * ficha and the filters of Pagos, then the reversal with both cheques recovered. Steps numbered as agreed.
 */

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";

interface Account {
  id: number;
  code: string | null;
  balance: string;
}

const cents = (amount: string) => Math.round(Number(amount) * 100);

async function account(request: APIRequestContext, code: string) {
  const all = await api<Account[]>(request, "GET", "/settlements/accounts?include_inactive=true");
  return all.find((a) => a.code === code)!;
}

async function balances(request: APIRequestContext) {
  const [cash, bank, portfolio] = await Promise.all(
    ["main_cash", "bank", "cheque_portfolio"].map((code) => account(request, code)),
  );
  return { cash: cents(cash.balance), bank: cents(bank.balance), portfolio: cents(portfolio.balance) };
}

/** A physical cheque of the portfolio (opening balance, as admin), received two days ago. */
async function portfolioCheque(request: APIRequestContext, number: string, amount: string) {
  const created = await api<{ id: number }>(request, "POST", "/settlements/cheques/opening-balance", {
    client_request_id: crypto.randomUUID(),
    business_date: businessDay(-2),
    kind: "PHYSICAL",
    payment_type: "COMMON",
    amount,
    issuer_bank_id: await galicia(request),
    branch_code: "123",
    account_ref: `9${number}`,
    cheque_number: number,
    issue_date: businessDay(-3),
    drawer_name: "Librador E2E",
    delivered_by_name: "Cliente E2E",
    physical_receipt_confirmed: true,
  });
  return created.id;
}

async function chequeState(request: APIRequestContext, id: number) {
  return (await api<{ state: string }>(request, "GET", `/settlements/cheques/${id}`)).state;
}

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test("R6 end to end: a payment with cash, a transfer and two cheques, the ficha, the filters and its reversal", async ({
  page,
}, testInfo) => {
  const mobile = testInfo.project.name === "mobile";
  const run = `${Date.now()}${mobile ? "M" : "D"}`;
  const supplier = `Proveedor R6 final ${run}`;
  const numberA = `5${String(Date.now()).slice(-6)}${mobile ? 1 : 2}`;
  const numberB = `6${String(Date.now()).slice(-6)}${mobile ? 1 : 2}`;

  // 1. The third party, 2. money in Caja and Banco and 3. two cheques of the portfolio (admin, by API).
  await login(page, "/rendiciones/pagos", adminEmail, adminPassword);
  const { id: supplierId } = await api<{ id: number }>(page.request, "POST", "/third-parties", {
    name: supplier,
    tax_id: null,
    notes: null,
  });
  const main = await account(page.request, "main_cash");
  const bank = await account(page.request, "bank");
  for (const target of [main, bank]) {
    await api(page.request, "POST", "/settlements/operations/opening-balance", {
      client_request_id: crypto.randomUUID(),
      business_date: businessDay(-2),
      account_id: target.id,
      amount: "100000.00",
    });
  }
  const chequeA = await portfolioCheque(page.request, numberA, "40000.00");
  const chequeB = await portfolioCheque(page.request, numberB, "25000.00");
  const before = await balances(page.request);

  // 4. Operator: one payment with Caja + transferencia + cheque A + cheque B.
  await page.context().clearCookies();
  await login(page, "/rendiciones/pagos", operatorEmail, operatorPassword);
  await page.getByRole("button", { name: "Registrar pago" }).click();
  const form = page.getByRole("dialog", { name: "Registrar pago" });
  await form.getByLabel("Tercero").selectOption({ label: supplier });
  await form.getByLabel("Concepto").selectOption("MERCHANDISE");
  await form.getByLabel("Total del pago").fill("95.000");
  await form.getByLabel("Caja").selectOption(String(main.id));
  await form.getByLabel("Importe del medio 1").fill("10.000");
  await form.getByRole("button", { name: "Agregar medio de pago" }).click();
  await form.getByLabel("Medio de pago").nth(1).selectOption("TRANSFER");
  await form.getByLabel("Banco").selectOption(String(bank.id));
  await form.getByLabel("Importe del medio 2").fill("20.000");
  await form.getByRole("button", { name: "Agregar medio de pago" }).click();
  await form.getByLabel("Medio de pago").nth(2).selectOption("CHEQUE");
  await form.getByLabel("Cheque de la cartera").nth(0).selectOption(String(chequeA));
  await form.getByRole("button", { name: "Agregar medio de pago" }).click();
  await form.getByLabel("Medio de pago").nth(3).selectOption("CHEQUE");
  await form.getByLabel("Cheque de la cartera").nth(1).selectOption(String(chequeB));
  await expect(form.getByLabel("Importe del medio 4")).toHaveValue("$ 25.000,00");
  if (mobile) await noHorizontalScroll(page);
  await form.getByRole("button", { name: "Revisar pago" }).click();

  // 5. Confirm.
  const confirm = page.getByRole("dialog", { name: "Confirmá el pago" });
  const summary = confirm.getByTestId("payment-confirmation");
  await expect(summary).toContainText(`Cheque n.º ${numberA} desde Cartera de cheques-$ 40.000,00`);
  await expect(summary).toContainText(`Cheque n.º ${numberB} desde Cartera de cheques-$ 25.000,00`);
  await expect(summary).toContainText("Pagos por mercadería+$ 95.000,00");
  await confirm.getByRole("button", { name: "Confirmar pago" }).click();
  await expect(confirm).toBeHidden();
  const notice = page.getByRole("status").filter({ hasText: "registrado por $ 95.000,00" });
  await expect(notice).toBeVisible();

  // 6. Balances and 7. both cheques Entregado.
  expect(await balances(page.request)).toEqual({
    cash: before.cash - 1_000_000,
    bank: before.bank - 2_000_000,
    portfolio: before.portfolio - 6_500_000,
  });
  expect([await chequeState(page.request, chequeA), await chequeState(page.request, chequeB)]).toEqual([
    "DELIVERED",
    "DELIVERED",
  ]);
  await notice.getByRole("link", { name: "Ver el pago" }).click();
  await expect(page.getByTestId(`payment-cheque-state-${chequeA}`)).toHaveText("Entregado");
  await expect(page.getByTestId(`payment-cheque-state-${chequeB}`)).toHaveText("Entregado");
  const detail = new URL(page.url()).pathname;
  const paymentId = Number(detail.split("/").at(-1));

  // 8. The third party's ficha: the payment, the total in effect, the means and the delivered cheques.
  await page.context().clearCookies();
  await login(page, `/terceros/${supplierId}`, adminEmail, adminPassword);
  const ficha = page.getByTestId("third-party-payments");
  await expect(ficha.getByTestId("third-party-not-a-debt")).toHaveText(
    "Estos importes muestran pagos registrados. DistriCom no está calculando deuda pendiente con este tercero.",
  );
  await expect(ficha.getByTestId("total-count")).toHaveText("1");
  await expect(ficha.getByTestId("total-amount")).toHaveText("$ 95.000,00");
  await expect(ficha.getByTestId("by-concept-MERCHANDISE")).toContainText("$ 95.000,00");
  await expect(ficha.getByTestId("by-method-CASH")).toContainText("$ 10.000,00");
  await expect(ficha.getByTestId("by-method-TRANSFER")).toContainText("$ 20.000,00");
  await expect(ficha.getByTestId("by-method-CHEQUE")).toContainText("$ 65.000,00");
  const cheques = ficha.getByTestId("third-party-cheques");
  const rowA = mobile
    ? cheques.getByTestId(`delivered-cheque-card-${chequeA}`)
    : cheques.getByTestId(`delivered-cheque-${chequeA}`);
  await expect(rowA).toContainText(numberA);
  await expect(rowA).toContainText("Entregado");
  await expect(rowA.getByRole("link", { name: `Ver pago n.º ${paymentId}` })).toHaveAttribute("href", detail);
  await expect(rowA.getByRole("link", { name: `Ver cheque n.º ${numberA}` })).toHaveAttribute(
    "href",
    `/rendiciones/cheques/${chequeA}`,
  );
  await expect(ficha).toContainText(numberB);
  if (mobile) await noHorizontalScroll(page);

  // 9. Filters of Pagos, applied by the backend: the mixed payment appears under each of its means.
  await page.goto(`/rendiciones/pagos?tercero=${supplierId}`);
  const listSummary = page.getByTestId("payments-summary");
  await expect(listSummary).toContainText("1 pago con estos filtros");
  for (const method of ["CASH", "TRANSFER", "CHEQUE"]) {
    await page.getByLabel("Medio").selectOption(method);
    await expect(listSummary).toContainText("1 pago con estos filtros · total vigente $ 95.000,00");
  }
  await page.getByLabel("N.º de cheque").fill(numberB);
  await expect(listSummary).toContainText("1 pago con estos filtros");
  await page.getByLabel("Concepto").selectOption("REFUND");
  await expect(page.getByText("No hay pagos", { exact: true })).toBeVisible();
  if (mobile) await noHorizontalScroll(page);

  // 10. The reversal is refused until every cheque is confirmed as recovered.
  await page.goto(detail);
  await page.getByRole("button", { name: "Revertir pago" }).click();
  const reverse = page.getByRole("dialog", { name: "Revertir pago" });
  await reverse.getByLabel("Motivo").fill("El proveedor devolvió los cheques (E2E final R6)");
  await reverse.getByRole("button", { name: "Revertir pago" }).click();
  await expect(reverse.getByRole("alert")).toContainText("Confirmá que recuperaste cada cheque");
  expect(await chequeState(page.request, chequeA)).toBe("DELIVERED");

  // 11. Both recovered, 12. reversed.
  await reverse.getByRole("checkbox", { name: new RegExp(`Recuperé el cheque n.º ${numberA}`) }).check();
  await reverse.getByRole("checkbox", { name: new RegExp(`Recuperé el cheque n.º ${numberB}`) }).check();
  await reverse.getByRole("button", { name: "Revertir pago" }).click();
  await expect(reverse).toBeHidden();

  // 13. Caja and Banco restored, 14. cheques back in Cartera, 15. the payment stays as Revertido.
  expect(await balances(page.request)).toEqual(before);
  expect([await chequeState(page.request, chequeA), await chequeState(page.request, chequeB)]).toEqual([
    "IN_PORTFOLIO",
    "IN_PORTFOLIO",
  ]);
  await expect(page.getByTestId("payment-state")).toHaveText("Revertido");
  if (mobile) await noHorizontalScroll(page);

  // 16. The third party's total in effect stops counting it; 17. the cheques show as recovered.
  await page.goto(`/terceros/${supplierId}`);
  await expect(ficha.getByTestId("total-count")).toHaveText("0");
  await expect(ficha.getByTestId("total-amount")).toHaveText("$ 0,00");
  await expect(ficha.getByTestId("total-reversed")).toHaveText("1");
  await expect(ficha.getByTestId("by-method-CHEQUE")).toContainText("$ 0,00");
  const recoveredA = mobile
    ? cheques.getByTestId(`delivered-cheque-card-${chequeA}`)
    : cheques.getByTestId(`delivered-cheque-${chequeA}`);
  await expect(recoveredA).toContainText("Recuperado");
  await expect(recoveredA).toContainText("En cartera");
  await expect(
    mobile ? ficha.getByRole("list", { name: "Pagos" }) : ficha.getByTestId(`payment-row-${paymentId}`),
  ).toContainText("Revertido");

  // 18. On a phone, nothing scrolls sideways.
  if (mobile) await noHorizontalScroll(page);
});
